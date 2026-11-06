"""The reports endpoint against real data."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import Lead, LeadEvent, Message
from app.models.enums import Channel, DeliveryStatus, LeadCategory, LeadEventType, MessageDirection
from app.schemas.mailbox import MailboxCreate
from app.services.mailboxes import MailboxService
from tests.integration.conftest import CreateUser, bearer, login
from tests.integration.test_leads_api import _lead
from tests.integration.test_mailboxes import NEW_MAILBOX

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    from cryptography.fernet import Fernet  # noqa: PLC0415
    from pydantic import SecretStr  # noqa: PLC0415

    return get_settings().model_copy(
        update={"secrets_encryption_key": SecretStr(Fernet.generate_key().decode())}
    )


@pytest.fixture
async def headers(
    api: AsyncClient, create_user: CreateUser, db_session: AsyncSession
) -> dict[str, str]:
    await create_user("rep@tawasol.test")
    await db_session.commit()
    return bearer(await login(api, "rep@tawasol.test"))


async def _report(api: AsyncClient, headers: dict[str, str], **params: str | int) -> Any:
    response = await api.get("/api/v1/reports", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _age(session: AsyncSession, lead_id: uuid.UUID, created: datetime) -> None:
    await session.execute(update(Lead).where(Lead.id == lead_id).values(created_at=created))
    await session.commit()


async def test_volume_is_bucketed_by_day_in_the_reporting_time_zone(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    lead_id = await _lead(db_session)
    # 22:30 UTC is already the next day in Dubai (UTC+4).
    yesterday = (datetime.now(UTC) - timedelta(days=1)).replace(hour=22, minute=30)
    await _age(db_session, lead_id, yesterday)
    await _lead(db_session, channel=Channel.WHATSAPP)

    report = await _report(api, headers, days=7)
    utc = await _report(api, headers, days=7, timezone="UTC")

    dubai_day = (yesterday + timedelta(hours=4)).date().isoformat()
    by_day = {d["date"]: d for d in report["volume"]}
    assert by_day[dubai_day]["email"] == 1
    assert {d["date"]: d for d in utc["volume"]}[yesterday.date().isoformat()]["email"] == 1
    assert len(report["volume"]) == 7  # seven local days, empty ones too, today partial
    assert report["by_source"] == {"email": 1, "whatsapp": 1}


async def test_categories_and_spam(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    await _lead(db_session)
    await _lead(db_session)
    await _lead(db_session, category=LeadCategory.SPAM)

    report = await _report(api, headers)

    assert report["categories"][0] == {"category": "sales_opportunity", "count": 2}
    assert {"category": "spam", "count": 1} in report["categories"]
    assert (report["total"], report["spam"]) == (3, 1)


async def test_the_funnel_counts_the_furthest_stage_reached(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    won = await _lead(db_session)
    lost = await _lead(db_session)
    fresh = await _lead(db_session)
    for lead_id, steps in (
        (won, ["contacted", "qualified", "converted"]),
        (lost, ["contacted", "qualified", "lost"]),
    ):
        for status in steps:
            await api.patch(f"/api/v1/leads/{lead_id}", json={"status": status}, headers=headers)
    await _lead(db_session, category=LeadCategory.SPAM)  # not in the funnel

    funnel = (await _report(api, headers))["funnel"]

    # The lost lead still reached qualified; the untouched one is only "created".
    assert funnel == {
        "created": 3,
        "contacted": 2,
        "qualified": 2,
        "converted": 1,
        "lost": 1,
        "conversion_rate": 0.333,
    }
    assert fresh is not None


async def test_response_time_counts_people_not_spam_or_lost_moves(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    answered = await _lead(db_session)
    binned = await _lead(db_session)
    await _age(db_session, answered, datetime.now(UTC) - timedelta(minutes=50))
    await _age(db_session, binned, datetime.now(UTC) - timedelta(minutes=50))
    await api.patch(f"/api/v1/leads/{answered}", json={"status": "contacted"}, headers=headers)
    await api.patch(f"/api/v1/leads/{binned}", json={"status": "lost"}, headers=headers)

    response = (await _report(api, headers))["response"]

    assert (response["responded"], response["unanswered"]) == (1, 1)
    assert 49 <= response["median_minutes"] <= 51
    # Both are high priority (score 63). The lost one stopped the SLA clock, so only the
    # answered one is judged: within the 60-minute SLA.
    assert (response["urgent"], response["urgent_within_sla"]) == (1, 1)


async def test_high_value_leads(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    top = await _lead(db_session, intent=100, urgency=100, business_value=100)
    await _lead(db_session)  # 63: high, counts
    await _lead(
        db_session, intent=10, urgency=10, business_value=10, company_name=None, job_title=None
    )
    await _lead(db_session, category=LeadCategory.SPAM)  # not in the share's denominator

    high_value = (await _report(api, headers))["high_value"]

    assert (high_value["threshold"], high_value["count"]) == (60, 2)
    assert high_value["top"][0]["id"] == str(top)
    assert high_value["share_of_leads"] == 0.667


async def test_old_leads_are_outside_the_period(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    lead_id = await _lead(db_session)
    await _age(db_session, lead_id, datetime.now(UTC) - timedelta(days=40))

    assert (await _report(api, headers, days=30))["total"] == 0
    assert (await _report(api, headers, days=90))["total"] == 1


async def test_bad_parameters(api: AsyncClient, headers: dict[str, str]) -> None:
    assert (await api.get("/api/v1/reports?days=0", headers=headers)).status_code == 422
    assert (await api.get("/api/v1/reports?days=400", headers=headers)).status_code == 422
    bad_zone = await api.get("/api/v1/reports?timezone=Mars/Olympus", headers=headers)
    assert bad_zone.status_code == 422
    assert "time zone" in bad_zone.json()["error"]["message"]
    assert (await api.get("/api/v1/reports")).status_code == 401


async def test_auto_replies_count_only_once_sent(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    await _lead(db_session)  # its auto-reply is queued, never sent, in tests

    assert (await _report(api, headers))["auto_replies_sent"] == 0


async def test_a_reply_counts_as_a_response_but_a_system_change_does_not(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str], settings: Settings
) -> None:
    mailbox = await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    await db_session.commit()
    replied = await _lead(db_session, mailbox_id=mailbox.id)
    moved_by_system = await _lead(db_session)
    await api.post(f"/api/v1/leads/{replied}/reply", json={"body": "Hi"}, headers=headers)
    db_session.add(
        LeadEvent(
            lead_id=moved_by_system,
            type=LeadEventType.STATUS_CHANGED,
            actor_id=None,  # not a person
            payload={"from": "new", "to": "contacted"},
        )
    )
    await db_session.commit()

    response = (await _report(api, headers))["response"]

    assert (response["responded"], response["unanswered"]) == (1, 1)


async def test_leads_marked_spam_are_left_out_of_the_funnel(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    await _lead(db_session)
    marked = await _lead(db_session)  # classified as a sales lead, then marked spam
    await api.patch(f"/api/v1/leads/{marked}", json={"status": "spam"}, headers=headers)

    report = await _report(api, headers)

    assert report["spam"] == 1
    assert report["funnel"]["created"] == 1
    assert report["response"]["responded"] + report["response"]["unanswered"] == 1


async def test_auto_replies_count_when_they_left(
    api: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    for status in (DeliveryStatus.SENT, DeliveryStatus.READ, DeliveryStatus.FAILED):
        lead_id = await _lead(db_session)
        db_session.add(
            Message(
                lead_id=lead_id,
                channel=Channel.EMAIL,
                direction=MessageDirection.OUTBOUND,
                sender_address="sales@example.com",
                body_text="Thanks",
                is_auto_reply=True,
                delivery_status=status,
                occurred_at=datetime.now(UTC),
            )
        )
    await db_session.commit()

    assert (await _report(api, headers))["auto_replies_sent"] == 2
