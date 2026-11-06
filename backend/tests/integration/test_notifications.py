"""Alerts for high-priority leads: raised once, delivered per channel, escalated on SLA."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.v1.notifications import notification_events
from app.core.config import Settings, get_settings
from app.integrations.pg_listener import RESYNC
from app.integrations.smtp import SendError, SmtpCredentials
from app.models import AppSetting, Contact, Lead, LeadEvent, Notification, User
from app.models.enums import (
    Channel,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    NotificationKind,
    Priority,
    Role,
)
from app.schemas.alerts import Alert
from app.schemas.app_settings import NotificationSettings
from app.schemas.inbound import InboundMessage
from app.schemas.mailbox import MailboxCreate
from app.services.ingestion import IngestionService
from app.services.lead_pipeline import LeadPipeline
from app.services.mailboxes import MailboxService
from app.services.notifications import NotificationService
from app.workers import names
from tests.fakes.ai import FakeAnalyzer, FakeEmbedder, analysis
from tests.integration.conftest import CreateUser, bearer, login, queued_jobs
from tests.integration.test_mailboxes import NEW_MAILBOX

pytestmark = pytest.mark.integration

CRITICAL_BODY = "Urgent RFP: pricing for 200 seats"


class FakeEmailSender:
    def __init__(self, *errors: SendError) -> None:
        self.errors = list(errors)
        self.sent: list[tuple[EmailMessage, SmtpCredentials]] = []

    async def send(self, message: EmailMessage, credentials: SmtpCredentials) -> None:
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((message, credentials))


class FakeChat:
    name = "slack"

    def __init__(self, *errors: SendError) -> None:
        self.errors = list(errors)
        self.alerts: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        if self.errors:
            raise self.errors.pop(0)
        self.alerts.append(alert)


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "secrets_encryption_key": SecretStr(Fernet.generate_key().decode()),
            "slack_webhook_url": SecretStr("https://hooks.example.test/x"),
            "frontend_url": "https://leads.example.test",
        }
    )


async def _lead(
    session: AsyncSession, body: str = "Please send a quote", **overrides: object
) -> uuid.UUID:
    result = await IngestionService(session).ingest(
        InboundMessage(
            channel=Channel.EMAIL,
            external_id=f"<{uuid.uuid4()}@gulfbuild.ae>",
            sender_address=f"buyer{uuid.uuid4().hex[:6]}@gulfbuild.ae",
            sender_name="Omar Haddad",
            subject="Quote",
            body_text=body,
            occurred_at=datetime.now(UTC),
        )
    )
    assert result.lead_id is not None
    await LeadPipeline(session, FakeAnalyzer(analysis(**overrides)), FakeEmbedder()).process(
        result.lead_id, final_attempt=False
    )
    return result.lead_id


async def _critical(session: AsyncSession) -> uuid.UUID:
    return await _lead(session, CRITICAL_BODY, intent=100, urgency=100, business_value=100)


async def _team(create_user: CreateUser, session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    rep = await create_user("rep@tawasol.test")
    admin = await create_user("boss@tawasol.test", role=Role.ADMIN)
    await session.commit()
    return rep.id, admin.id


async def _notified(session: AsyncSession, lead_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    rows = await session.execute(
        select(Notification.user_id, Notification.kind).where(Notification.lead_id == lead_id)
    )
    return [(row.user_id, row.kind) for row in rows]


async def test_a_high_lead_alerts_its_owner_once(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    rep, _admin = await _team(create_user, db_session)
    lead_id = await _lead(db_session)
    lead = await db_session.get(Lead, lead_id)
    assert lead is not None
    assert lead.priority == Priority.HIGH
    [job] = await queued_jobs(db_session, names.NOTIFY_LEAD)
    assert job["args"] == {"lead_id": str(lead_id), "kind": "high_priority_lead"}
    service = NotificationService(db_session, settings)

    assert await service.raise_alert(lead_id, NotificationKind.HIGH_PRIORITY_LEAD)
    assert not await service.raise_alert(lead_id, NotificationKind.HIGH_PRIORITY_LEAD)

    assert await _notified(db_session, lead_id) == [(rep, NotificationKind.HIGH_PRIORITY_LEAD)]
    deliveries = await queued_jobs(db_session, names.DELIVER_ALERT)
    assert [(d["args"]["channel"], d["args"]["target"]) for d in deliveries] == [
        ("email", "rep@tawasol.test")
    ]
    [title] = (await db_session.scalars(select(Notification.title))).all()
    assert title.startswith("High-priority lead: Gulf Build LLC (score ")


async def test_a_critical_lead_escalates_to_admins_the_list_and_chat(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    rep, admin = await _team(create_user, db_session)
    db_session.add(
        AppSetting(
            key=NotificationSettings.KEY,
            value=NotificationSettings(escalation_emails=["manager@example.com"]).model_dump(
                mode="json"
            ),
        )
    )
    await db_session.commit()
    lead_id = await _critical(db_session)
    assert (await db_session.get(Lead, lead_id)).priority == Priority.CRITICAL  # type: ignore[union-attr]

    assert await NotificationService(db_session, settings).raise_alert(
        lead_id, NotificationKind.ESCALATION
    )

    assert sorted(await _notified(db_session, lead_id)) == sorted(
        [(rep, NotificationKind.ESCALATION), (admin, NotificationKind.ESCALATION)]
    )
    deliveries = await queued_jobs(db_session, names.DELIVER_ALERT)
    assert [(d["args"]["channel"], d["args"]["target"]) for d in deliveries] == [
        ("email", "rep@tawasol.test"),
        ("email", "manager@example.com"),
        ("slack", None),
    ]
    event = await db_session.scalar(
        select(LeadEvent).where(
            LeadEvent.lead_id == lead_id, LeadEvent.type == LeadEventType.ESCALATED
        )
    )
    assert event is not None
    assert event.payload["kind"] == "escalation"


async def test_an_unassigned_high_lead_alerts_the_admins(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    admin = await create_user("boss@tawasol.test", role=Role.ADMIN)
    await db_session.commit()
    lead_id = await _lead(db_session)  # no sales reps: nobody is assigned

    await NotificationService(db_session, settings).raise_alert(
        lead_id, NotificationKind.HIGH_PRIORITY_LEAD
    )

    assert await _notified(db_session, lead_id) == [(admin.id, NotificationKind.HIGH_PRIORITY_LEAD)]


async def test_low_and_spam_leads_raise_nothing(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    await _team(create_user, db_session)
    spam = await _lead(db_session, category=LeadCategory.SPAM)

    assert await queued_jobs(db_session, names.NOTIFY_LEAD) == []
    assert not await NotificationService(db_session, settings).raise_alert(
        spam, NotificationKind.ESCALATION
    )


async def test_a_closed_lead_raises_nothing(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    await _team(create_user, db_session)
    lead_id = await _lead(db_session)
    await db_session.execute(update(Lead).where(Lead.id == lead_id).values(status=LeadStatus.LOST))
    await db_session.commit()

    assert not await NotificationService(db_session, settings).raise_alert(
        lead_id, NotificationKind.HIGH_PRIORITY_LEAD
    )


async def test_email_alerts_go_out_from_a_mailbox(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    await _team(create_user, db_session)
    await MailboxService(db_session, settings).create(MailboxCreate.model_validate(NEW_MAILBOX))
    await db_session.commit()
    lead_id = await _lead(db_session)
    email = FakeEmailSender()

    delivered = await NotificationService(db_session, settings).deliver(
        lead_id,
        NotificationKind.HIGH_PRIORITY_LEAD,
        "email",
        "rep@tawasol.test",
        email=email,
        chat={},
        final_attempt=False,
    )

    assert delivered
    [(message, credentials)] = email.sent
    assert message["To"] == "rep@tawasol.test"
    assert f"https://leads.example.test/leads/{lead_id}" in message.get_content()
    assert credentials.host == "smtp.example.com"


async def test_without_a_mailbox_email_alerts_fail_without_retrying(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    await _team(create_user, db_session)
    lead_id = await _lead(db_session)

    delivered = await NotificationService(db_session, settings).deliver(
        lead_id,
        NotificationKind.HIGH_PRIORITY_LEAD,
        "email",
        "rep@tawasol.test",
        email=FakeEmailSender(),
        chat={},
        final_attempt=False,
    )

    assert not delivered
    failures = await db_session.scalars(
        select(LeadEvent.payload).where(LeadEvent.lead_id == lead_id)
    )
    assert any(p.get("kind") == "high_priority_lead_delivery_failed" for p in failures)


async def test_chat_alerts_retry_then_give_up(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    await _team(create_user, db_session)
    lead_id = await _critical(db_session)
    chat = FakeChat(SendError("503", retryable=True), SendError("503", retryable=True))
    service = NotificationService(db_session, settings)

    async def deliver(*, final: bool) -> bool:
        return await service.deliver(
            lead_id,
            NotificationKind.ESCALATION,
            "slack",
            None,
            email=FakeEmailSender(),
            chat={"slack": chat},
            final_attempt=final,
        )

    with pytest.raises(SendError):
        await deliver(final=False)
    assert not await deliver(final=True)
    assert await deliver(final=False)  # a later alert gets through
    assert chat.alerts[0].title.startswith("Critical lead:")


async def _age_alerts(session: AsyncSession, lead_id: uuid.UUID, minutes: int) -> None:
    """Move the lead's scoring and alerts into the past (the SLA clock starts at scoring)."""
    await session.execute(
        update(LeadEvent)
        .where(
            LeadEvent.lead_id == lead_id,
            LeadEvent.type.in_(["scored", "notified", "escalated"]),
        )
        .values(created_at=datetime.now(UTC) - timedelta(minutes=minutes))
    )
    await session.execute(
        update(Lead)
        .where(Lead.id == lead_id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=minutes))
    )
    await session.commit()


async def test_unanswered_leads_escalate_after_the_sla_once(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    await _team(create_user, db_session)
    lead_id = await _lead(db_session)
    service = NotificationService(db_session, settings)
    await service.raise_alert(lead_id, NotificationKind.HIGH_PRIORITY_LEAD)
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()

    assert await service.sweep_sla() == 0  # alerted just now: within the SLA
    await _age_alerts(db_session, lead_id, minutes=61)
    assert await service.sweep_sla() == 1
    [job] = await queued_jobs(db_session, names.NOTIFY_LEAD)
    assert job["args"]["kind"] == "sla_breach"

    assert await service.raise_alert(lead_id, NotificationKind.SLA_BREACH)
    assert await service.sweep_sla() == 0  # escalated already
    titles = await db_session.scalars(
        select(Notification.title).where(Notification.kind == NotificationKind.SLA_BREACH)
    )
    assert all(t.startswith("No response in 60 min") for t in titles)


@pytest.mark.parametrize("answered", ["status", "first_response"])
async def test_answered_leads_do_not_breach(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings, answered: str
) -> None:
    await _team(create_user, db_session)
    lead_id = await _lead(db_session)
    service = NotificationService(db_session, settings)
    await service.raise_alert(lead_id, NotificationKind.HIGH_PRIORITY_LEAD)
    await _age_alerts(db_session, lead_id, minutes=120)
    values = (
        {"status": LeadStatus.CONTACTED}
        if answered == "status"
        else {"first_response_at": datetime.now(UTC)}
    )
    await db_session.execute(update(Lead).where(Lead.id == lead_id).values(**values))
    await db_session.commit()

    assert await service.sweep_sla() == 0
    assert not await service.raise_alert(lead_id, NotificationKind.SLA_BREACH)


async def test_the_inbox_api(
    api: AsyncClient, db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    rep, _admin = await _team(create_user, db_session)
    for _ in range(2):
        await NotificationService(db_session, settings).raise_alert(
            await _lead(db_session), NotificationKind.HIGH_PRIORITY_LEAD
        )
    other = await create_user("other@tawasol.test")
    stranger = Notification(user_id=other.id, kind=NotificationKind.ASSIGNMENT, title="x", body="")
    db_session.add(stranger)
    await db_session.commit()
    token = bearer(await login(api, "rep@tawasol.test"))

    page = (await api.get("/api/v1/notifications", headers=token)).json()
    assert (len(page["items"]), page["unread"]) == (2, 2)
    first = page["items"][0]["id"]

    read = await api.post(f"/api/v1/notifications/{first}/read", headers=token)
    assert read.status_code == 200
    assert read.json()["read_at"] is not None
    unread = (await api.get("/api/v1/notifications?unread_only=true", headers=token)).json()
    assert (len(unread["items"]), unread["unread"]) == (1, 1)

    foreign = await api.post(f"/api/v1/notifications/{stranger.id}/read", headers=token)
    assert foreign.status_code == 404
    assert (await api.post("/api/v1/notifications/read-all", headers=token)).json() == {
        "updated": 1
    }
    assert (await api.get("/api/v1/notifications", headers=token)).json()["unread"] == 0
    assert rep is not None


async def test_the_inbox_needs_a_signed_in_user(api: AsyncClient) -> None:
    assert (await api.get("/api/v1/notifications")).status_code == 401
    assert (await api.get("/api/v1/notifications/stream")).status_code == 401


async def test_the_stream_yields_notifications_until_the_token_expires() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait(json.dumps({"id": "n1"}))
    until = datetime.now(UTC) + timedelta(milliseconds=300)

    events = [event async for event in notification_events(queue, until=until)]

    assert [e["event"] for e in events] == ["ready", "notification", "expired"]
    assert json.loads(events[1]["data"]) == {"id": "n1"}


async def test_the_sla_holds_even_if_the_first_alert_never_went_out(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    # Regression: the SLA clock started at the first alert, so an alert job that gave up
    # also cancelled the escalation.
    await _team(create_user, db_session)
    lead_id = await _lead(db_session)
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))  # its notify job died
    await db_session.commit()
    await _age_alerts(db_session, lead_id, minutes=61)

    assert await NotificationService(db_session, settings).sweep_sla() == 2
    kinds = sorted(job["args"]["kind"] for job in await queued_jobs(db_session, names.NOTIFY_LEAD))
    assert kinds == ["high_priority_lead", "sla_breach"]  # the lost alert is raised again too


async def test_a_lead_rescored_lower_before_its_alert_is_not_alerted(
    db_session: AsyncSession, create_user: CreateUser, settings: Settings
) -> None:
    await _team(create_user, db_session)
    lead_id = await _lead(db_session)
    await db_session.execute(
        update(Lead).where(Lead.id == lead_id).values(priority=Priority.MEDIUM, score=45)
    )
    await db_session.commit()
    service = NotificationService(db_session, settings)

    assert not await service.raise_alert(lead_id, NotificationKind.HIGH_PRIORITY_LEAD)
    assert not await service.raise_alert(lead_id, NotificationKind.SLA_BREACH)
    assert await _notified(db_session, lead_id) == []


async def test_concurrent_alert_jobs_raise_one_alert(
    engine: AsyncEngine, settings: Settings
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(User(email="race-rep@race-alert.test", full_name="R", role=Role.SALES_REP))
        await session.commit()
        lead_id = await _lead(session)
    try:

        async def raise_once() -> bool:
            async with sessions() as session:
                return await NotificationService(session, settings).raise_alert(
                    lead_id, NotificationKind.HIGH_PRIORITY_LEAD
                )

        results = await asyncio.gather(*(raise_once() for _ in range(4)))

        assert sorted(results) == [False, False, False, True]
        async with sessions() as session:
            assert len(await _notified(session, lead_id)) == 1
    finally:
        async with sessions() as session:
            await session.execute(delete(Lead).where(Lead.id == lead_id))
            await session.execute(delete(Contact).where(Contact.email.like("buyer%@gulfbuild.ae")))
            await session.execute(delete(User).where(User.email == "race-rep@race-alert.test"))
            await session.execute(text("DELETE FROM procrastinate_jobs"))
            await session.commit()


async def test_paging_with_before(
    api: AsyncClient, db_session: AsyncSession, create_user: CreateUser
) -> None:
    rep = await create_user("pager@tawasol.test")
    for i in range(3):
        db_session.add(
            Notification(
                user_id=rep.id,
                kind=NotificationKind.ASSIGNMENT,
                title=f"n{i}",
                body="",
                created_at=datetime.now(UTC) - timedelta(minutes=i),
            )
        )
    await db_session.commit()
    token = bearer(await login(api, "pager@tawasol.test"))

    first = (await api.get("/api/v1/notifications?limit=2", headers=token)).json()["items"]
    rest = (
        await api.get(
            "/api/v1/notifications", params={"before": first[-1]["created_at"]}, headers=token
        )
    ).json()["items"]

    assert [n["title"] for n in first + rest] == ["n0", "n1", "n2"]


async def test_the_stream_tells_clients_to_reload_after_a_reconnect() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait(RESYNC)
    until = datetime.now(UTC) + timedelta(milliseconds=200)

    events = [e["event"] async for e in notification_events(queue, until=until)]

    assert events == ["ready", "resync", "expired"]
