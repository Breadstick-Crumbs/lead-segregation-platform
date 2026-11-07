"""WhatsApp, Slack, Teams and Microsoft sign-in, configured from the dashboard.

Values saved here override the same settings from `.env`, which stay as the fallback, so a
deployment configured by environment keeps working. Secrets are Fernet-encrypted at rest
and never sent back to the browser: the admin only sees whether each one is set, and where
from. Everything that talks to these services gets its settings through `effective()`, so
a change applies to the API, the worker and the MCP tools without a restart."""

import time
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import SecretStr
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import SecretBox, SecretsNotConfiguredError
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.integrations.chat_webhooks import SlackNotifier, TeamsNotifier
from app.integrations.microsoft_entra import EntraClient, EntraError
from app.integrations.smtp import SendError
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.models import AppSetting
from app.models.enums import NotificationKind
from app.schemas.alerts import Alert
from app.schemas.integrations import (
    PLAIN_FIELDS,
    SECRET_FIELDS,
    ConnectionCheck,
    FieldState,
    IntegrationConfig,
    IntegrationUpdate,
)

logger = get_logger(__name__)

STORE_KEY = "integrations"  # an app_settings row, kept out of the generic settings API
CACHE_SECONDS = 15  # how long another process (the worker) may run on the previous values

Source = Literal["dashboard", "environment"]


class SecretsKeyMissingError(AppError):
    status_code = 409
    code = "secrets_key_missing"


@dataclass
class _Cached:
    stored: dict[str, Any]
    loaded_at: float


_cache: _Cached | None = None


def invalidate_cache() -> None:
    global _cache  # noqa: PLW0603  (one process-wide cache, reset on save and in tests)
    _cache = None


async def _stored(session: AsyncSession) -> dict[str, Any]:
    global _cache  # noqa: PLW0603
    if _cache is not None and time.monotonic() - _cache.loaded_at < CACHE_SECONDS:
        return _cache.stored
    # populate_existing: the save is an upsert, so an already-loaded row would be stale.
    row = await session.get(AppSetting, STORE_KEY, populate_existing=True)
    stored = dict(row.value) if row is not None else {}
    _cache = _Cached(stored, time.monotonic())
    return stored


def _decrypt(settings: Settings, name: str, token: str) -> str | None:
    try:
        return SecretBox(settings).decrypt(token)
    except SecretsNotConfiguredError:
        # Missing or rotated key: fall back to .env rather than fail every request.
        logger.error("integration_secret_unreadable", field=name)
        return None


async def effective(session: AsyncSession, base: Settings) -> Settings:
    """`base` (from the environment) with the dashboard's values on top."""
    stored = await _stored(session)
    if not stored:
        return base
    update: dict[str, Any] = {}
    for name in PLAIN_FIELDS:
        if stored.get(name):
            update[name] = stored[name]
    for name in SECRET_FIELDS:
        if stored.get(name):
            value = _decrypt(base, name, stored[name])
            if value is not None:
                update[name] = SecretStr(value)
    return base.model_copy(update=update) if update else base


class IntegrationSettings:
    def __init__(self, session: AsyncSession, base: Settings) -> None:
        self._session = session
        self._base = base  # the environment's values, without the dashboard's

    async def read(self) -> IntegrationConfig:
        row = await self._session.get(AppSetting, STORE_KEY, populate_existing=True)
        stored = dict(row.value) if row is not None else {}
        fields: dict[str, FieldState] = {}
        for name in PLAIN_FIELDS:
            env_value = getattr(self._base, name)
            value = stored.get(name) or env_value
            fields[name] = FieldState(value=value, source=self._source(stored, name, env_value))
        for name in SECRET_FIELDS:
            env_value = getattr(self._base, name)
            fields[name] = FieldState(value=None, source=self._source(stored, name, env_value))
        return IntegrationConfig(
            fields=fields,
            whatsapp_webhook_url=f"{self._base.frontend_url.rstrip('/')}/api/v1/webhooks/whatsapp",
            microsoft_redirect_uri=self._base.entra_callback_url,
            secrets_key=self._base.secrets_encryption_key is not None,
        )

    @staticmethod
    def _source(stored: dict[str, Any], name: str, env_value: object) -> Source | None:
        if stored.get(name):
            return "dashboard"
        return "environment" if env_value else None

    async def save(self, update: IntegrationUpdate, *, by_user_id: object) -> IntegrationConfig:
        """Apply the fields that were sent: a value sets it, `null` removes the dashboard's
        value (so `.env` applies again), and an omitted field stays as it is."""
        row = await self._session.get(
            AppSetting, STORE_KEY, with_for_update=True, populate_existing=True
        )
        stored = dict(row.value) if row is not None else {}
        changes = update.model_dump(exclude_unset=True)
        for name, value in changes.items():
            if value is None:
                stored.pop(name, None)
            elif name in SECRET_FIELDS:
                try:
                    stored[name] = SecretBox(self._base).encrypt(value.get_secret_value())
                except SecretsNotConfiguredError as exc:
                    raise SecretsKeyMissingError(
                        "Set SECRETS_ENCRYPTION_KEY on the server before saving secrets here"
                    ) from exc
            else:
                stored[name] = value
        await self._session.execute(
            insert(AppSetting)
            .values(key=STORE_KEY, value=stored, updated_by_id=by_user_id)
            .on_conflict_do_update(
                index_elements=[AppSetting.key],
                set_={"value": stored, "updated_by_id": by_user_id},
            )
        )
        await self._session.commit()
        invalidate_cache()
        logger.info("integrations_saved", fields=sorted(changes))  # names only, never values
        return await self.read()


TEST_ALERT = Alert(
    kind=NotificationKind.ESCALATION,
    title="Test alert from Tawasol Leads",
    summary="If you can read this, escalations will reach this channel.",
    facts=(("Sent from", "Settings → Integrations"),),
    url="",
)


async def _check_chat(name: str, settings: Settings) -> ConnectionCheck:
    url = settings.slack_webhook_url if name == "slack" else settings.teams_webhook_url
    if url is None:
        return ConnectionCheck(ok=False, detail="No webhook URL saved yet")
    notifier = SlackNotifier if name == "slack" else TeamsNotifier
    await notifier(url.get_secret_value()).send(replace(TEST_ALERT, url=settings.frontend_url))
    return ConnectionCheck(ok=True, detail="Test message sent. Check the channel.")


async def _check_whatsapp(settings: Settings) -> ConnectionCheck:
    if not settings.whatsapp_phone_number_id:
        return ConnectionCheck(ok=False, detail="No phone number ID saved yet")
    number = await WhatsAppClient.from_settings(settings).describe_number(
        settings.whatsapp_phone_number_id
    )
    missing = [
        label
        for label, value in (
            ("app secret", settings.whatsapp_app_secret),
            ("verify token", settings.whatsapp_verify_token),
        )
        if value is None
    ]
    if missing:
        return ConnectionCheck(
            ok=False,
            detail=f"Sending works for {number}, but the {' and '.join(missing)} "
            "must be set to receive messages",
        )
    return ConnectionCheck(ok=True, detail=f"Connected to {number}")


async def _check_microsoft(settings: Settings) -> ConnectionCheck:
    await EntraClient.from_settings(settings).check_credentials()
    return ConnectionCheck(ok=True, detail="Tenant, client ID and secret are accepted by Microsoft")


async def check_connection(name: str, settings: Settings) -> ConnectionCheck:
    """Try the saved configuration for real. Slack and Teams get a visible test message."""
    checks = {
        "slack": lambda: _check_chat("slack", settings),
        "teams": lambda: _check_chat("teams", settings),
        "whatsapp": lambda: _check_whatsapp(settings),
        "microsoft": lambda: _check_microsoft(settings),
    }
    check = checks.get(name)
    if check is None:
        raise NotFoundError(f"No integration named {name!r}")
    try:
        return await check()
    except (SendError, WhatsAppError, EntraError) as exc:
        return ConnectionCheck(ok=False, detail=str(exc))
