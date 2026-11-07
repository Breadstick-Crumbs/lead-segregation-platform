"""WhatsApp, Slack, Teams and Microsoft configured from the dashboard: stored encrypted,
never read back, layered over the environment, and used by the code that talks to them."""

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.integrations.chat_webhooks import SlackNotifier
from app.integrations.microsoft_entra import EntraClient, EntraError
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.main import create_app
from app.models import AppSetting
from app.models.enums import Role
from app.services import integrations
from app.services.notifications import NotificationService
from tests.fakes.messages import wa_text, whatsapp_payload
from tests.integration.conftest import CreateUser, bearer, login

pytestmark = pytest.mark.integration

CONFIG = "/api/v1/settings/integrations/config"
TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "66666666-7777-8888-9999-000000000000"
SLACK = "https://hooks.slack.com/services/T000/B000/dashboard"


@pytest.fixture
def env() -> Settings:
    """The server's environment: a key to encrypt with, and WhatsApp values the dashboard
    can override."""
    return get_settings().model_copy(
        update={
            "secrets_encryption_key": SecretStr(Fernet.generate_key().decode()),
            "whatsapp_phone_number_id": "1000",
            "whatsapp_app_secret": SecretStr("env-app-secret"),
            "whatsapp_verify_token": SecretStr("env-verify"),
            "whatsapp_access_token": None,
            "slack_webhook_url": None,
            "teams_webhook_url": None,
            "entra_tenant_id": None,
            "entra_client_id": None,
            "entra_client_secret": None,
        }
    )


@pytest.fixture
async def api(db_session: AsyncSession, env: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def admin(
    api: AsyncClient, create_user: CreateUser, db_session: AsyncSession
) -> dict[str, str]:
    await create_user("boss@tawasol.test", role=Role.ADMIN)
    await db_session.commit()
    return bearer(await login(api, "boss@tawasol.test"))


def _signed(secret: str) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(whatsapp_payload(wa_text("Need a quote"))).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": signature, "Content-Type": "application/json"}


async def test_reading_shows_sources_and_never_secrets(
    api: AsyncClient, admin: dict[str, str], env: Settings
) -> None:
    config = (await api.get(CONFIG, headers=admin)).json()
    fields = config["fields"]
    assert fields["whatsapp_phone_number_id"] == {"value": "1000", "source": "environment"}
    assert fields["whatsapp_app_secret"] == {"value": None, "source": "environment"}
    assert fields["slack_webhook_url"] == {"value": None, "source": None}
    assert config["whatsapp_webhook_url"].endswith("/api/v1/webhooks/whatsapp")
    assert config["microsoft_redirect_uri"].endswith("/api/v1/auth/microsoft/callback")
    assert config["secrets_key"] is True


async def test_saved_secrets_are_encrypted_and_not_returned(
    api: AsyncClient, admin: dict[str, str], db_session: AsyncSession
) -> None:
    response = await api.patch(
        CONFIG,
        headers=admin,
        json={"whatsapp_access_token": "EAAG-dashboard-token", "slack_webhook_url": SLACK},
    )
    assert response.status_code == 200, response.text
    assert "EAAG-dashboard-token" not in response.text
    assert "hooks.slack.com" not in response.text
    fields = response.json()["fields"]
    assert fields["whatsapp_access_token"]["source"] == "dashboard"
    assert fields["slack_webhook_url"]["source"] == "dashboard"

    row = await db_session.get(AppSetting, integrations.STORE_KEY)
    assert row is not None
    stored = json.dumps(row.value)
    assert "EAAG-dashboard-token" not in stored
    assert "hooks.slack.com" not in stored
    # The generic settings API doesn't list it either.
    documents = (await api.get("/api/v1/settings", headers=admin)).json()
    assert integrations.STORE_KEY not in {d["key"] for d in documents}


async def test_the_webhook_uses_the_dashboards_verify_token_and_app_secret(
    api: AsyncClient, admin: dict[str, str]
) -> None:
    url = "/api/v1/webhooks/whatsapp"
    challenge = {"hub.mode": "subscribe", "hub.challenge": "42"}
    assert (await api.get(url, params={**challenge, "hub.verify_token": "env-verify"})).text == "42"

    await api.patch(
        CONFIG,
        headers=admin,
        json={"whatsapp_verify_token": "dash-verify", "whatsapp_app_secret": "dash-app-secret"},
    )
    old = await api.get(url, params={**challenge, "hub.verify_token": "env-verify"})
    new = await api.get(url, params={**challenge, "hub.verify_token": "dash-verify"})
    assert (old.status_code, new.status_code, new.text) == (403, 200, "42")

    body, headers = _signed("env-app-secret")
    assert (await api.post(url, content=body, headers=headers)).status_code == 401
    body, headers = _signed("dash-app-secret")
    assert (await api.post(url, content=body, headers=headers)).status_code == 200


async def test_removing_a_value_falls_back_to_the_environment(
    api: AsyncClient, admin: dict[str, str]
) -> None:
    await api.patch(CONFIG, headers=admin, json={"whatsapp_phone_number_id": "2000"})
    fields = (await api.get(CONFIG, headers=admin)).json()["fields"]
    assert fields["whatsapp_phone_number_id"] == {"value": "2000", "source": "dashboard"}

    response = await api.patch(CONFIG, headers=admin, json={"whatsapp_phone_number_id": None})
    assert response.json()["fields"]["whatsapp_phone_number_id"] == {
        "value": "1000",
        "source": "environment",
    }


async def test_omitted_fields_are_left_alone(api: AsyncClient, admin: dict[str, str]) -> None:
    await api.patch(CONFIG, headers=admin, json={"slack_webhook_url": SLACK})
    await api.patch(CONFIG, headers=admin, json={"whatsapp_phone_number_id": "3000"})
    fields = (await api.get(CONFIG, headers=admin)).json()["fields"]
    assert fields["slack_webhook_url"]["source"] == "dashboard"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("whatsapp_phone_number_id", "+971 50 123"),
        ("entra_tenant_id", "contoso.onmicrosoft.com"),
        ("entra_client_id", "not-a-guid"),
        ("slack_webhook_url", "https://evil.example/hook"),
        ("teams_webhook_url", "http://plain.example/hook"),
        ("teams_webhook_url", "https://10.0.0.5/internal"),  # SSRF into the server's network
        ("teams_webhook_url", "https://evil.example/x.logic.azure.com"),
        ("teams_webhook_url", "https://prod-1.logic.azure.com.evil.example/workflows"),
        ("teams_webhook_url", "https://user@prod-1.westus.logic.azure.com/workflows"),
        ("whatsapp_access_token", "has spaces in it"),
    ],
)
async def test_invalid_values_are_refused(
    api: AsyncClient, admin: dict[str, str], field: str, value: str
) -> None:
    response = await api.patch(CONFIG, headers=admin, json={field: value})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "url",
    [
        "https://prod-12.westeurope.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke",
        "https://default1234.56.environment.api.powerplatform.com/powerautomate/automations/x",
        "https://contoso.webhook.office.com/webhookb2/abc",
    ],
)
async def test_teams_workflow_urls_are_accepted(
    api: AsyncClient, admin: dict[str, str], url: str
) -> None:
    response = await api.patch(CONFIG, headers=admin, json={"teams_webhook_url": url})
    assert response.status_code == 200, response.text


async def test_only_admins_can_see_or_change_integrations(
    api: AsyncClient, create_user: CreateUser, db_session: AsyncSession
) -> None:
    await create_user("rep@tawasol.test")
    await db_session.commit()
    rep = bearer(await login(api, "rep@tawasol.test"))
    assert (await api.get(CONFIG, headers=rep)).status_code == 403
    assert (await api.patch(CONFIG, headers=rep, json={})).status_code == 403
    assert (
        await api.post("/api/v1/settings/integrations/slack/check", headers=rep)
    ).status_code == 403


async def test_secrets_need_the_encryption_key(
    db_session: AsyncSession, env: Settings, create_user: CreateUser
) -> None:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: env.model_copy(
        update={"secrets_encryption_key": None}
    )
    await create_user("boss@tawasol.test", role=Role.ADMIN)
    await db_session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        headers = bearer(await login(c, "boss@tawasol.test"))
        secret = await c.patch(CONFIG, headers=headers, json={"slack_webhook_url": SLACK})
        plain = await c.patch(CONFIG, headers=headers, json={"whatsapp_phone_number_id": "4000"})
    assert secret.status_code == 409
    assert "SECRETS_ENCRYPTION_KEY" in secret.json()["error"]["message"]
    assert plain.status_code == 200


async def test_microsoft_sign_in_appears_once_configured(
    api: AsyncClient, admin: dict[str, str]
) -> None:
    providers = "/api/v1/auth/providers"
    assert (await api.get(providers)).json()["microsoft"] is False
    await api.patch(
        CONFIG,
        headers=admin,
        json={
            "entra_tenant_id": TENANT,
            "entra_client_id": CLIENT,
            "entra_client_secret": "s3cr3t",
        },
    )
    assert (await api.get(providers)).json()["microsoft"] is True
    login_redirect = await api.get("/api/v1/auth/microsoft/login")
    assert login_redirect.status_code == 303
    assert TENANT in login_redirect.headers["location"]
    assert CLIENT in login_redirect.headers["location"]


async def test_alerts_go_to_the_dashboards_slack_webhook(
    api: AsyncClient, admin: dict[str, str], db_session: AsyncSession, env: Settings
) -> None:
    await api.patch(CONFIG, headers=admin, json={"slack_webhook_url": SLACK})
    settings = await integrations.effective(db_session, env)
    assert settings.slack_webhook_url is not None
    assert settings.slack_webhook_url.get_secret_value() == SLACK
    assert settings.whatsapp_phone_number_id == "1000"  # untouched values come from env
    deliveries = await NotificationService(db_session, settings)._external_deliveries(
        integrations.TEST_ALERT.kind, None
    )
    assert ("slack", None) in deliveries


async def test_connection_checks_report_each_service(
    api: AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def check(name: str) -> Any:
        return api.post(f"/api/v1/settings/integrations/{name}/check", headers=admin)

    assert (await check("slack")).json() == {"ok": False, "detail": "No webhook URL saved yet"}
    assert (await check("nope")).status_code == 404

    sent: list[str] = []

    async def fake_send(self: SlackNotifier, alert: Any) -> None:
        sent.append(alert.title)

    monkeypatch.setattr(SlackNotifier, "send", fake_send)
    await api.patch(CONFIG, headers=admin, json={"slack_webhook_url": SLACK})
    assert (await check("slack")).json()["ok"] is True
    assert sent == ["Test alert from Tawasol Leads"]

    async def number(self: WhatsAppClient, phone_number_id: str) -> str:
        return f"+971 4 000 {phone_number_id} (Tawasol)"

    monkeypatch.setattr(WhatsAppClient, "describe_number", number)
    await api.patch(CONFIG, headers=admin, json={"whatsapp_access_token": "EAAG-token"})
    whatsapp = (await check("whatsapp")).json()
    assert whatsapp == {"ok": True, "detail": "Connected to +971 4 000 1000 (Tawasol)"}

    async def refused(self: EntraClient) -> None:
        raise EntraError("AADSTS7000215: Invalid client secret provided.")

    monkeypatch.setattr(EntraClient, "check_credentials", refused)
    await api.patch(
        CONFIG,
        headers=admin,
        json={"entra_tenant_id": TENANT, "entra_client_id": CLIENT, "entra_client_secret": "x"},
    )
    assert (await check("microsoft")).json() == {
        "ok": False,
        "detail": "AADSTS7000215: Invalid client secret provided.",
    }


async def test_describe_number_and_credential_check_read_the_real_responses() -> None:
    def graph(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer EAAG-token"
        return httpx.Response(
            200, json={"display_phone_number": "+1 555 0100", "verified_name": "Tawasol"}
        )

    client = WhatsAppClient(
        access_token="EAAG-token", graph_version="v26.0", transport=httpx.MockTransport(graph)
    )
    assert await client.describe_number("1000") == "+1 555 0100 (Tawasol)"

    denied = WhatsAppClient(
        access_token="bad",
        graph_version="v26.0",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(401, json={"error": {"code": 190, "message": "Bad token"}})
        ),
    )
    with pytest.raises(WhatsAppError, match="190 Bad token"):
        await denied.describe_number("1000")

    entra = EntraClient(
        tenant_id=TENANT,
        client_id=CLIENT,
        client_secret="wrong",
        redirect_uri="http://localhost/cb",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "AADSTS7000215: Invalid client secret.\nTrace ID: x",
                },
            )
        ),
    )
    with pytest.raises(EntraError, match=r"^AADSTS7000215: Invalid client secret\.$"):
        await entra.check_credentials()
