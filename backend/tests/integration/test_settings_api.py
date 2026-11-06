"""Admin settings: documents, validation, the scoring preview and integration status."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Role
from tests.integration.conftest import CreateUser, bearer, login
from tests.integration.test_leads_api import _lead

pytestmark = pytest.mark.integration


@pytest.fixture
async def admin(
    api: AsyncClient, create_user: CreateUser, db_session: AsyncSession
) -> dict[str, str]:
    await create_user("boss@tawasol.test", role=Role.ADMIN)
    await create_user("rep@tawasol.test")
    await db_session.commit()
    return bearer(await login(api, "boss@tawasol.test"))


async def test_every_document_is_listed_with_defaults(
    api: AsyncClient, admin: dict[str, str]
) -> None:
    documents = {
        d["key"]: d["value"] for d in (await api.get("/api/v1/settings", headers=admin)).json()
    }

    assert documents["scoring.weights"]["intent"] == 25
    assert documents["scoring.thresholds"]["auto_reply_above"] == 20
    assert documents["notifications"]["escalation_emails"] == []
    assert {"scoring.keywords", "auto_reply.templates", "ingestion", "ai.similarity"} <= set(
        documents
    )


async def test_saving_validates_the_whole_document(api: AsyncClient, admin: dict[str, str]) -> None:
    weights = {
        "intent": 40,
        "urgency": 10,
        "business_value": 20,
        "keywords": 10,
        "company": 10,
        "history": 10,
    }

    saved = await api.put("/api/v1/settings/scoring.weights", json=weights, headers=admin)
    bad = await api.put(
        "/api/v1/settings/scoring.thresholds",
        json={"auto_reply_above": 70, "high_at": 60, "critical_at": 80},
        headers=admin,
    )
    unknown = await api.put("/api/v1/settings/nope", json={}, headers=admin)

    assert saved.status_code == 200
    assert saved.json()["value"]["intent"] == 40
    assert bad.status_code == 422
    assert "auto_reply_above < high_at" in bad.json()["error"]["message"]
    assert unknown.status_code == 404
    documents = {
        d["key"]: d["value"] for d in (await api.get("/api/v1/settings", headers=admin)).json()
    }
    assert documents["scoring.weights"]["intent"] == 40
    assert documents["scoring.thresholds"]["auto_reply_above"] == 20  # unchanged


async def test_settings_are_admin_only(api: AsyncClient, admin: dict[str, str]) -> None:
    rep = bearer(await login(api, "rep@tawasol.test"))

    assert (await api.get("/api/v1/settings", headers=rep)).status_code == 403
    assert (await api.put("/api/v1/settings/ingestion", json={}, headers=rep)).status_code == 403
    assert (await api.get("/api/v1/settings/integrations", headers=rep)).status_code == 403


async def test_the_scoring_preview_rescores_recent_leads(
    api: AsyncClient, admin: dict[str, str], db_session: AsyncSession
) -> None:
    await _lead(db_session)  # scores 63: high
    only_intent = {
        "intent": 1,
        "urgency": 0,
        "business_value": 0,
        "keywords": 0,
        "company": 0,
        "history": 0,
    }
    defaults = {"auto_reply_above": 20, "high_at": 60, "critical_at": 80}

    preview = await api.post(
        "/api/v1/settings/scoring-preview",
        json={"weights": only_intent, "thresholds": defaults},
        headers=admin,
    )

    [item] = preview.json()["items"]
    assert (item["current_score"], item["current_priority"]) == (63, "high")
    assert (item["new_score"], item["new_priority"]) == (85, "critical")
    assert preview.json()["changed"] == 1


async def test_integration_status_reveals_no_secrets(
    api: AsyncClient, admin: dict[str, str]
) -> None:
    status = (await api.get("/api/v1/settings/integrations", headers=admin)).json()

    assert set(status) == {
        "openai",
        "whatsapp",
        "whatsapp_phone_number_id",
        "microsoft_sign_in",
        "slack",
        "teams",
        "secrets_key",
        "active_mailboxes",
    }
    assert all(isinstance(status[k], bool) for k in ("openai", "slack", "teams", "whatsapp"))
