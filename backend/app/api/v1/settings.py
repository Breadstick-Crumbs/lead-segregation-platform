"""Admin settings: scoring, thresholds, keywords, templates, notifications; integrations."""

from typing import Any

from fastapi import APIRouter

from app.api.deps import AdminUser, EnvSettingsDep, SessionDep, SettingsDep
from app.schemas.integrations import ConnectionCheck, IntegrationConfig, IntegrationUpdate
from app.schemas.settings_api import (
    IntegrationStatus,
    ScoringPreview,
    ScoringPreviewItem,
    ScoringPreviewRequest,
    SettingsDocument,
)
from app.services.integrations import IntegrationSettings, check_connection
from app.services.leads import display_name
from app.services.settings_admin import SettingsAdmin

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def list_settings(
    _: AdminUser, session: SessionDep, settings: SettingsDep
) -> list[SettingsDocument]:
    """Every settings document, with defaults filled in for ones never saved."""
    documents = await SettingsAdmin(session, settings).documents()
    return [
        SettingsDocument(key=key, value=value.model_dump(mode="json"))
        for key, value in documents.items()
    ]


@router.put("/{key}")
async def save_setting(
    key: str,
    body: dict[str, Any],
    _: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
) -> SettingsDocument:
    """Replace one document (e.g. `scoring.weights`). It is validated as a whole, so an
    invalid value changes nothing."""
    saved = await SettingsAdmin(session, settings).save(key, body)
    return SettingsDocument(key=key, value=saved.model_dump(mode="json"))


@router.post("/scoring-preview")
async def preview_scoring(
    body: ScoringPreviewRequest, _: AdminUser, session: SessionDep, settings: SettingsDep
) -> ScoringPreview:
    """How the latest scored leads would score with these weights and thresholds. Nothing is
    saved."""
    rows = await SettingsAdmin(session, settings).scoring_preview(body.weights, body.thresholds)
    items = [
        ScoringPreviewItem(
            lead_id=lead.id,
            display_name=display_name(contact),
            current_score=lead.score or 0,
            current_priority=lead.priority,
            new_score=new_score,
            new_priority=new_priority,
        )
        for lead, contact, new_score, new_priority in rows
    ]
    return ScoringPreview(
        items=items, changed=sum(1 for i in items if i.new_priority != i.current_priority)
    )


@router.get("/integrations")
async def integration_status(
    _: AdminUser, session: SessionDep, settings: SettingsDep
) -> IntegrationStatus:
    """Which external services are configured (never their secrets)."""
    return IntegrationStatus.model_validate(await SettingsAdmin(session, settings).integrations())


@router.get("/integrations/config")
async def get_integration_config(
    _: AdminUser, session: SessionDep, env: EnvSettingsDep
) -> IntegrationConfig:
    """WhatsApp, Slack, Teams and Microsoft settings: plain values, and for secrets only
    whether each is set and where from (the dashboard or the server's environment)."""
    return await IntegrationSettings(session, env).read()


@router.patch("/integrations/config")
async def update_integration_config(
    body: IntegrationUpdate, admin: AdminUser, session: SessionDep, env: EnvSettingsDep
) -> IntegrationConfig:
    """Change only the fields sent. `null` removes the dashboard's value, so the server's
    environment value (if any) applies again. Takes effect within seconds everywhere."""
    return await IntegrationSettings(session, env).save(body, by_user_id=admin.id)


@router.post("/integrations/{name}/check")
async def check_integration(name: str, _: AdminUser, settings: SettingsDep) -> ConnectionCheck:
    """Try the saved configuration against the real service: `whatsapp`, `slack`, `teams`
    or `microsoft`. Slack and Teams receive a visible test message."""
    return await check_connection(name, settings)
