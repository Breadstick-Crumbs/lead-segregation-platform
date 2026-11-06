"""The dashboard's lead API: listing and filters, detail, similar leads, and actions."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import Lead, LeadEvent, Message, Notification, User
from app.models.enums import Channel, LeadCategory, LeadEventType, LeadStatus, Role
from app.schemas.inbound import InboundMessage
from app.schemas.mailbox import MailboxCreate
from app.services.auto_reply import AutoReplyOutcome, AutoReplyService
from app.services.ingestion import IngestionService
from app.services.lead_pipeline import LeadPipeline
from app.services.leads import LeadService
from app.services.mailboxes import MailboxService
from app.workers import names
from tests.fakes.ai import FakeAnalyzer, FakeEmbedder, analysis
from tests.integration.conftest import CreateUser, bearer, login, queued_jobs
from tests.integration.test_auto_reply import FakeEmailSender
from tests.integration.test_mailboxes import NEW_MAILBOX

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={"secrets_encryption_key": SecretStr(Fernet.generate_key().decode())}
    )


@pytest.fixture
async def team(create_user: CreateUser, db_session: AsyncSession) -> dict[str, uuid.UUID]:
    rep = await create_user("rep@tawasol.test", full_name="Rania Rep")
    other = await create_user("other@tawasol.test", full_name="Omar Other")
    admin = await create_user("boss@tawasol.test", role=Role.ADMIN, full_name="Bushra Boss")
    await db_session.commit()
    return {"rep": rep.id, "other": other.id, "admin": admin.id}


async def _lead(
    session: AsyncSession,
    *,
    body: str = "Please send a quote for 40 seats",
    sender: str | None = None,
    channel: Channel = Channel.EMAIL,
    mailbox_id: uuid.UUID | None = None,
    embedding_axis: int = 0,
    occurred_at: datetime | None = None,
    **overrides: object,
) -> uuid.UUID:
    is_email = channel == Channel.EMAIL
    result = await IngestionService(session).ingest(
        InboundMessage(
            channel=channel,
            external_id=f"<{uuid.uuid4()}@x.test>" if is_email else f"wamid.{uuid.uuid4()}",
            sender_address=sender
            or (f"buyer{uuid.uuid4().hex[:6]}@gulfbuild.ae" if is_email else "+971501112233"),
            sender_name="Omar Haddad",
            subject="Quote" if is_email else None,
            body_text=body,
            mailbox_id=mailbox_id,
            occurred_at=occurred_at or datetime.now(UTC),
        )
    )
    assert result.lead_id is not None
    await LeadPipeline(
        session,
        FakeAnalyzer(analysis(**overrides)),
        FakeEmbedder(default=embedding_axis),
    ).process(result.lead_id, final_attempt=False)
    return result.lead_id


async def _token(api: AsyncClient, email: str = "rep@tawasol.test") -> dict[str, str]:
    return bearer(await login(api, email))


async def test_list_filters_search_and_sort(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    hot = await _lead(db_session, intent=100, urgency=100, business_value=100)
    spam = await _lead(db_session, body="cheap seo", category=LeadCategory.SPAM)
    await _lead(db_session, company_name="Desert Rose Trading")
    headers = await _token(api)

    everything = (await api.get("/api/v1/leads", headers=headers)).json()
    assert everything["total"] == 3
    by_score = (await api.get("/api/v1/leads?sort=score", headers=headers)).json()["items"]
    assert by_score[0]["id"] == str(hot)

    only_spam = (await api.get("/api/v1/leads?category=spam", headers=headers)).json()
    assert [i["id"] for i in only_spam["items"]] == [str(spam)]
    several = await api.get(
        "/api/v1/leads",
        params=[("category", "spam"), ("category", "sales_opportunity")],
        headers=headers,
    )
    assert several.json()["total"] == 3

    found = (await api.get("/api/v1/leads?q=desert", headers=headers)).json()["items"]
    assert [i["contact"]["company_name"] for i in found] == ["Desert Rose Trading"]
    assert (await api.get("/api/v1/leads?q=100%25", headers=headers)).json()["total"] == 0

    high = (await api.get("/api/v1/leads?score_min=75", headers=headers)).json()["items"]
    assert [i["id"] for i in high] == [str(hot)]
    paged = (await api.get("/api/v1/leads?page=2&page_size=2", headers=headers)).json()
    assert (len(paged["items"]), paged["total"]) == (1, 3)


async def test_owner_filters(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    for _ in range(2):
        await _lead(db_session)  # round-robin: one each to rep and other
    await _lead(db_session, category=LeadCategory.SPAM)  # spam: never assigned
    headers = await _token(api)

    mine = (await api.get("/api/v1/leads?owner=me", headers=headers)).json()["items"]
    unassigned = (await api.get("/api/v1/leads?owner=unassigned", headers=headers)).json()
    by_id = await api.get(f"/api/v1/leads?owner={team['other']}", headers=headers)

    assert [i["owner"]["id"] for i in mine] == [str(team["rep"])]
    assert unassigned["total"] == 1
    assert by_id.json()["total"] == 1


async def test_detail_shows_the_conversation_score_and_timeline(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    lead_id = await _lead(db_session)
    headers = await _token(api)

    detail = (await api.get(f"/api/v1/leads/{lead_id}", headers=headers)).json()

    assert detail["messages"][0]["body_text"] == "Please send a quote for 40 seats"
    assert detail["latest_score"]["breakdown"]["intent"]["weight"] == 25
    assert detail["latest_score"]["rationale"]
    # Events written in one transaction share a timestamp, so compare as a set.
    assert {"created", "message_received", "classified", "scored"} <= {
        e["type"] for e in detail["events"]
    }
    assert detail["can_reply"] is False  # no mailbox to send from
    assert "mailbox" in detail["reply_blocked_reason"]
    assert (await api.get(f"/api/v1/leads/{uuid.uuid4()}", headers=headers)).status_code == 404


async def test_similar_leads_come_from_pgvector(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    lead_id = await _lead(db_session, embedding_axis=5)
    twin = await _lead(db_session, embedding_axis=5)
    await _lead(db_session, embedding_axis=9)  # unrelated
    headers = await _token(api)

    similar = (await api.get(f"/api/v1/leads/{lead_id}/similar", headers=headers)).json()

    assert [s["id"] for s in similar] == [str(twin)]
    assert similar[0]["similarity"] == 1.0


async def test_moving_a_lead_on_records_the_first_response(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    lead_id = await _lead(db_session)
    headers = await _token(api)

    contacted = await api.patch(
        f"/api/v1/leads/{lead_id}", json={"status": "contacted"}, headers=headers
    )
    assert contacted.status_code == 200
    assert contacted.json()["first_response_at"] is not None
    converted = await api.patch(
        f"/api/v1/leads/{lead_id}", json={"status": "converted"}, headers=headers
    )
    assert converted.json()["converted_at"] is not None
    reopened = await api.patch(
        f"/api/v1/leads/{lead_id}", json={"status": "qualified"}, headers=headers
    )
    assert reopened.json()["converted_at"] is None

    changes = await db_session.scalars(
        select(LeadEvent.payload).where(
            LeadEvent.lead_id == lead_id, LeadEvent.type == LeadEventType.STATUS_CHANGED
        )
    )
    assert [(c["from"], c["to"]) for c in changes] == [
        ("new", "contacted"),
        ("contacted", "converted"),
        ("converted", "qualified"),
    ]


async def test_only_admins_reassign_but_reps_can_claim(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    await db_session.execute(update(Lead).values(owner_id=None))
    lead_id = await _lead(db_session, category=LeadCategory.SPAM)  # unassigned
    rep = await _token(api)

    to_other = await api.patch(
        f"/api/v1/leads/{lead_id}", json={"owner_id": str(team["other"])}, headers=rep
    )
    assert to_other.status_code == 403
    claimed = await api.patch(
        f"/api/v1/leads/{lead_id}", json={"owner_id": str(team["rep"])}, headers=rep
    )
    assert claimed.json()["owner"]["id"] == str(team["rep"])
    steal_back = await api.patch(f"/api/v1/leads/{lead_id}", json={"owner_id": None}, headers=rep)
    assert steal_back.status_code == 403

    admin = await _token(api, "boss@tawasol.test")
    moved = await api.patch(
        f"/api/v1/leads/{lead_id}", json={"owner_id": str(team["other"])}, headers=admin
    )
    assert moved.json()["owner"]["full_name"] == "Omar Other"
    [note] = (
        await db_session.scalars(select(Notification).where(Notification.user_id == team["other"]))
    ).all()
    assert note.title.startswith("Bushra Boss assigned you")
    unassigned = await api.patch(f"/api/v1/leads/{lead_id}", json={"owner_id": None}, headers=admin)
    assert unassigned.json()["owner"] is None


async def test_a_reply_is_queued_threaded_and_counts_as_contact(
    api: AsyncClient,
    db_session: AsyncSession,
    team: dict[str, uuid.UUID],
    settings: Settings,
) -> None:
    mailbox = await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    await db_session.commit()
    lead_id = await _lead(db_session, mailbox_id=mailbox.id)
    headers = await _token(api)

    response = await api.post(
        f"/api/v1/leads/{lead_id}/reply", json={"body": "Hi Omar, quote attached."}, headers=headers
    )

    assert response.status_code == 202
    reply = response.json()
    assert (reply["direction"], reply["delivery_status"], reply["is_auto_reply"]) == (
        "outbound",
        "queued",
        False,
    )
    assert reply["subject"] == "Re: Quote"
    assert reply["sent_by_user_id"] == str(team["rep"])
    lead = await db_session.get(Lead, lead_id)
    assert lead is not None
    await db_session.refresh(lead)
    assert lead.status == LeadStatus.CONTACTED
    assert lead.first_response_at is not None
    [job] = await queued_jobs(db_session, names.SEND_MESSAGE)
    assert job["args"] == {"message_id": reply["id"]}


async def test_whatsapp_replies_respect_the_24_hour_window(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    lead_id = await _lead(
        db_session, channel=Channel.WHATSAPP, occurred_at=datetime.now(UTC) - timedelta(hours=30)
    )
    headers = await _token(api)

    detail = (await api.get(f"/api/v1/leads/{lead_id}", headers=headers)).json()
    response = await api.post(
        f"/api/v1/leads/{lead_id}/reply", json={"body": "Hi"}, headers=headers
    )

    assert detail["can_reply"] is False
    assert response.status_code == 409
    assert "24 hours" in response.json()["error"]["message"]


async def test_notes_and_validation(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    lead_id = await _lead(db_session)
    headers = await _token(api)

    note = await api.post(
        f"/api/v1/leads/{lead_id}/notes", json={"body": "Called, wants Monday"}, headers=headers
    )
    blank = await api.post(f"/api/v1/leads/{lead_id}/notes", json={"body": "   "}, headers=headers)

    assert note.status_code == 201
    assert note.json()["payload"] == {"text": "Called, wants Monday"}
    assert note.json()["actor_id"] == str(team["rep"])
    assert blank.status_code == 422


async def test_stats(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    hot = await _lead(db_session, intent=100, urgency=100, business_value=100)
    await _lead(db_session, category=LeadCategory.SPAM)
    won = await _lead(db_session)
    lost = await _lead(db_session)
    headers = await _token(api)
    for lead_id, status in ((won, "converted"), (lost, "lost")):
        await api.patch(f"/api/v1/leads/{lead_id}", json={"status": status}, headers=headers)

    stats = (await api.get("/api/v1/leads/stats", headers=headers)).json()

    assert stats["new_24h"] == 3  # spam isn't counted
    assert stats["high_priority_open"] == 1
    assert (stats["converted_30d"], stats["closed_30d"], stats["conversion_rate_30d"]) == (
        1,
        2,
        0.5,
    )
    assert hot is not None


async def test_leads_need_a_signed_in_user(api: AsyncClient) -> None:
    assert (await api.get("/api/v1/leads")).status_code == 401
    assert (await api.get("/api/v1/leads/stats")).status_code == 401


async def test_a_queued_reply_is_sent_by_the_worker(
    db_session: AsyncSession, team: dict[str, uuid.UUID], settings: Settings
) -> None:
    mailbox = await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    await db_session.commit()
    lead_id = await _lead(db_session, mailbox_id=mailbox.id)
    rep = await db_session.get(User, team["rep"])
    assert rep is not None
    reply = await LeadService(db_session, settings).reply(lead_id, "Quote attached", actor=rep)
    email = FakeEmailSender()

    outcome = await AutoReplyService(db_session, settings, email=email, whatsapp=None).send_message(
        reply.id, final_attempt=False
    )

    assert outcome == AutoReplyOutcome.SENT
    [(message, _)] = email.sent
    # Regression: a person's reply was marked Auto-Submitted like the acknowledgement.
    assert message["Auto-Submitted"] is None
    assert message["In-Reply-To"] is not None
    sent = await db_session.get(Message, reply.id)
    assert sent is not None
    await db_session.refresh(sent)
    assert sent.delivery_status == "sent"
    events = await db_session.scalars(select(LeadEvent.type).where(LeadEvent.lead_id == lead_id))
    assert LeadEventType.REPLIED in list(events)


async def test_stats_count_conversions_by_when_they_happened(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    # Regression: an old conversion touched today (reassigned) counted as this month's.
    old = await _lead(db_session)
    await db_session.execute(
        update(Lead)
        .where(Lead.id == old)
        .values(status=LeadStatus.CONVERTED, converted_at=datetime.now(UTC) - timedelta(days=90))
    )
    await db_session.commit()
    admin = await _token(api, "boss@tawasol.test")
    await api.patch(f"/api/v1/leads/{old}", json={"owner_id": str(team["other"])}, headers=admin)

    stats = (await api.get("/api/v1/leads/stats", headers=admin)).json()

    assert (stats["converted_30d"], stats["closed_30d"]) == (0, 0)


async def test_a_lead_marked_lost_counts_as_closed(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID]
) -> None:
    lead_id = await _lead(db_session)
    headers = await _token(api)
    await api.patch(f"/api/v1/leads/{lead_id}", json={"status": "lost"}, headers=headers)

    stats = (await api.get("/api/v1/leads/stats", headers=headers)).json()

    assert (stats["converted_30d"], stats["closed_30d"]) == (0, 1)


async def test_ownership_and_missing_lead_errors(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID], create_user: CreateUser
) -> None:
    owned = await _lead(db_session)  # round-robin gives it to a rep
    gone = await create_user("gone@tawasol.test", is_active=False)
    await db_session.commit()
    lead = await db_session.get(Lead, owned)
    assert lead is not None
    someone_else = "other@tawasol.test" if lead.owner_id == team["rep"] else "rep@tawasol.test"
    rep = await _token(api, someone_else)
    admin = await _token(api, "boss@tawasol.test")
    claimer = team["other"] if someone_else == "other@tawasol.test" else team["rep"]

    steal = await api.patch(f"/api/v1/leads/{owned}", json={"owner_id": str(claimer)}, headers=rep)
    inactive = await api.patch(
        f"/api/v1/leads/{owned}", json={"owner_id": str(gone.id)}, headers=admin
    )
    missing = uuid.uuid4()

    assert steal.status_code == 403
    assert inactive.status_code == 404
    assert (
        await api.patch(f"/api/v1/leads/{missing}", json={"status": "lost"}, headers=rep)
    ).status_code == 404
    assert (
        await api.post(f"/api/v1/leads/{missing}/reply", json={"body": "x"}, headers=rep)
    ).status_code == 404
    assert (
        await api.post(f"/api/v1/leads/{missing}/notes", json={"body": "x"}, headers=rep)
    ).status_code == 404


async def test_spam_leads_cannot_be_replied_to(
    api: AsyncClient, db_session: AsyncSession, team: dict[str, uuid.UUID], settings: Settings
) -> None:
    mailbox = await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    await db_session.commit()
    lead_id = await _lead(db_session, mailbox_id=mailbox.id, category=LeadCategory.SPAM)
    headers = await _token(api)

    response = await api.post(
        f"/api/v1/leads/{lead_id}/reply", json={"body": "Hi"}, headers=headers
    )

    assert response.status_code == 409
    assert "spam" in response.json()["error"]["message"]


async def test_hostile_list_parameters_are_rejected_or_cleaned(
    api: AsyncClient, team: dict[str, uuid.UUID]
) -> None:
    headers = await _token(api)

    assert (await api.get("/api/v1/leads?page=100000000000", headers=headers)).status_code == 422
    assert (
        await api.get("/api/v1/leads", params={"q": "gulf\x00build"}, headers=headers)
    ).status_code == 200


async def test_a_reply_is_sent_at_most_once(
    db_session: AsyncSession, team: dict[str, uuid.UUID], settings: Settings
) -> None:
    mailbox = await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    await db_session.commit()
    lead_id = await _lead(db_session, mailbox_id=mailbox.id)
    rep = await db_session.get(User, team["rep"])
    assert rep is not None
    reply = await LeadService(db_session, settings).reply(lead_id, "Quote attached", actor=rep)
    email = FakeEmailSender()
    sender = AutoReplyService(db_session, settings, email=email, whatsapp=None)

    assert await sender.send_message(reply.id, final_attempt=False) == AutoReplyOutcome.SENT
    assert await sender.send_message(reply.id, final_attempt=False) == AutoReplyOutcome.SKIPPED
    assert len(email.sent) == 1
