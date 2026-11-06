"""The admin settings screens: edit documents, preview scoring changes, show integrations."""

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models import Contact, Lead, LeadScore
from app.repositories.mailboxes import MailboxRepository
from app.schemas.app_settings import ScoringThresholds, ScoringWeights, SettingsModel
from app.services.app_settings import load_all, save_settings, settings_model
from app.services.scoring import rescore

PREVIEW_LEADS = 20


class InvalidSettingsError(AppError):
    status_code = 422
    code = "invalid_settings"


class SettingsAdmin:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def documents(self) -> dict[str, SettingsModel]:
        return await load_all(self._session)

    async def save(self, key: str, value: dict[str, Any]) -> SettingsModel:
        model = settings_model(key)
        try:
            validated = model.model_validate(value)
        except ValidationError as exc:
            # One readable line per problem, e.g. "high_at: Input should be ≤ 100".
            problems = "; ".join(
                f"{'.'.join(str(p) for p in error['loc']) or key}: {error['msg']}"
                for error in exc.errors()
            )
            raise InvalidSettingsError(problems) from exc
        await save_settings(self._session, validated)
        return validated

    async def scoring_preview(
        self, weights: ScoringWeights, thresholds: ScoringThresholds
    ) -> list[tuple[Lead, Contact, int, Any]]:
        """The latest scored leads, rescored with the proposed weights and thresholds."""
        latest = (
            select(LeadScore.lead_id, LeadScore.breakdown)
            .distinct(LeadScore.lead_id)
            .order_by(LeadScore.lead_id, LeadScore.created_at.desc())
            .subquery()
        )
        rows = await self._session.execute(
            select(Lead, Contact, latest.c.breakdown)
            .join(latest, latest.c.lead_id == Lead.id)
            .join(Contact, Contact.id == Lead.contact_id)
            .where(Lead.score.is_not(None))
            .order_by(Lead.created_at.desc())
            .limit(PREVIEW_LEADS)
        )
        return [
            (lead, contact, *rescore(breakdown, weights=weights, thresholds=thresholds))
            for lead, contact, breakdown in rows.all()
        ]

    async def integrations(self) -> dict[str, Any]:
        s = self._settings
        return {
            "openai": s.openai_api_key is not None,
            "whatsapp": bool(
                s.whatsapp_access_token and s.whatsapp_phone_number_id and s.whatsapp_app_secret
            ),
            "whatsapp_phone_number_id": s.whatsapp_phone_number_id,
            "microsoft_sign_in": s.entra_enabled,
            "slack": s.slack_webhook_url is not None,
            "teams": s.teams_webhook_url is not None,
            "secrets_key": s.secrets_encryption_key is not None,
            "active_mailboxes": len(await MailboxRepository(self._session).active_ids()),
        }
