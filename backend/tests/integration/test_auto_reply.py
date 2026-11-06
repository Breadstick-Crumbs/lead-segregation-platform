"""Auto-replies end to end: the pipeline queues them, the service sends them once."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.integrations.smtp import SendError, SmtpCredentials
from app.integrations.whatsapp import WhatsAppClient
from app.models import Lead, LeadEvent, Mailbox, Message
from app.models.enums import (
    Channel,
    DeliveryStatus,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MailboxAuthType,
)
from app.repositories.leads import DeliveryUpdate, MessageRepository
from app.schemas.inbound import InboundMessage
from app.schemas.mailbox import MailboxCreate
from app.services.auto_reply import AutoReplyOutcome, AutoReplyService
from app.services.ingestion import IngestionService
from app.services.lead_pipeline import LeadPipeline
from app.services.mailboxes import MailboxService
from app.services.whatsapp_inbox import WhatsAppInbox
from app.workers import names
from tests.fakes.ai import FakeAnalyzer, FakeEmbedder, analysis
from tests.fakes.messages import PHONE_NUMBER_ID, wa_status_payload
from tests.integration.conftest import queued_jobs
from tests.integration.test_mailboxes import NEW_MAILBOX

pytestmark = pytest.mark.integration


class FakeEmailSender:
    def __init__(self, *errors: SendError | Exception) -> None:
        self.errors = list(errors)  # raised in order, one per call
        self.sent: list[tuple[EmailMessage, SmtpCredentials]] = []

    async def send(self, message: EmailMessage, credentials: SmtpCredentials) -> None:
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((message, credentials))


class FakeGraph:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def client(self) -> WhatsAppClient:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content))
            return httpx.Response(
                200, json={"messages": [{"id": f"wamid.OUT{len(self.requests)}"}]}
            )

        return WhatsAppClient(
            access_token="t", graph_version="v26.0", transport=httpx.MockTransport(handle)
        )


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "secrets_encryption_key": SecretStr(Fernet.generate_key().decode()),
            "whatsapp_phone_number_id": PHONE_NUMBER_ID,
        }
    )


async def _mailbox(session: AsyncSession, settings: Settings) -> uuid.UUID:
    mailbox = await MailboxService(session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    await session.commit()
    return mailbox.id


def _email(mailbox_id: uuid.UUID, **overrides: object) -> InboundMessage:
    values: dict[str, object] = {
        "channel": Channel.EMAIL,
        "external_id": f"<{uuid.uuid4()}@gulfbuild.ae>",
        "sender_address": "omar@gulfbuild.ae",
        "sender_name": "Omar Haddad",
        "subject": "Quote request",
        "body_text": "Please send a quote for 40 seats this week.",
        "thread_refs": ("<earlier@gulfbuild.ae>",),
        "mailbox_id": mailbox_id,
        "occurred_at": datetime.now(UTC),
    }
    values.update(overrides)
    return InboundMessage(**values)  # type: ignore[arg-type]


def _whatsapp(**overrides: object) -> InboundMessage:
    values: dict[str, object] = {
        "channel": Channel.WHATSAPP,
        "external_id": f"wamid.IN.{uuid.uuid4()}",
        "sender_address": "+971501234567",
        "sender_name": "Fatima Al Mansoori",
        "body_text": "Need pricing for 15 users today",
        "occurred_at": datetime.now(UTC),
    }
    values.update(overrides)
    return InboundMessage(**values)  # type: ignore[arg-type]


async def _scored_lead(
    session: AsyncSession, message: InboundMessage, **analysis_overrides: object
) -> uuid.UUID:
    result = await IngestionService(session).ingest(message)
    assert result.lead_id is not None
    await LeadPipeline(
        session, FakeAnalyzer(analysis(**analysis_overrides)), FakeEmbedder()
    ).process(result.lead_id, final_attempt=False)
    return result.lead_id


def _service(
    session: AsyncSession,
    settings: Settings,
    email: FakeEmailSender | None = None,
    graph: FakeGraph | None = None,
) -> AutoReplyService:
    return AutoReplyService(
        session,
        settings,
        email=email or FakeEmailSender(),
        whatsapp=graph.client() if graph else None,
    )


async def _reply(session: AsyncSession, lead_id: uuid.UUID) -> Message:
    session.expire_all()
    reply = await session.scalar(
        select(Message).where(Message.lead_id == lead_id, Message.is_auto_reply)
    )
    assert reply is not None
    return reply


async def test_a_scored_email_lead_gets_one_threaded_reply(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    inbound = _email(mailbox_id)
    lead_id = await _scored_lead(db_session, inbound)
    jobs = await queued_jobs(db_session, names.SEND_AUTO_REPLY)
    assert [job["args"] for job in jobs] == [{"lead_id": str(lead_id)}]
    email = FakeEmailSender()

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SENT
    )

    [(message, credentials)] = email.sent
    assert message["To"] == "omar@gulfbuild.ae"
    assert message["From"] == "Sales inbox <sales@example.com>"
    assert message["Subject"] == "Re: Quote request"
    assert message["In-Reply-To"] == inbound.external_id
    assert message["References"] == f"<earlier@gulfbuild.ae> {inbound.external_id}"
    assert credentials.password == "imap-app-password"
    reply = await _reply(db_session, lead_id)
    assert reply.delivery_status == DeliveryStatus.SENT
    assert reply.external_id == message["Message-ID"]
    events = await db_session.scalars(
        select(LeadEvent.payload).where(
            LeadEvent.lead_id == lead_id, LeadEvent.type == LeadEventType.AUTO_REPLIED
        )
    )
    assert [e["status"] for e in events] == ["sent"]

    # A retried or duplicated job doesn't send again.
    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SKIPPED
    )
    assert len(email.sent) == 1


async def test_a_customer_reply_threads_back_onto_the_lead(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id))
    await _service(db_session, settings).send(lead_id, final_attempt=False)
    our_id = (await _reply(db_session, lead_id)).external_id
    assert our_id is not None

    answer = await IngestionService(db_session).ingest(
        _email(mailbox_id, body_text="Thanks, call me", thread_refs=(our_id,))
    )

    assert answer.lead_id == lead_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": LeadCategory.SPAM, "intent": 0},
        {"intent": 0, "urgency": 0, "business_value": 0, "company_name": None, "job_title": None},
    ],
    ids=["spam", "score_20_or_less"],
)
async def test_spam_and_low_scores_get_no_reply(
    db_session: AsyncSession, settings: Settings, overrides: dict[str, object]
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    message = _email(mailbox_id, sender_address="x@gmail.com", body_text="hello", subject=None)
    lead_id = await _scored_lead(db_session, message, **overrides)
    lead = await db_session.get(Lead, lead_id)
    assert lead is not None
    assert lead.score is not None
    assert lead.score <= 20

    assert await queued_jobs(db_session, names.SEND_AUTO_REPLY) == []
    email = FakeEmailSender()
    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SKIPPED
    )
    assert email.sent == []


async def test_mailing_lists_and_robots_are_never_answered(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id, suppress_auto_reply=True))
    email = FakeEmailSender()

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SKIPPED
    )
    assert email.sent == []


async def test_a_certainly_unsent_reply_is_retried(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id))
    email = FakeEmailSender(SendError("Couldn't connect", retryable=True))

    with pytest.raises(SendError):
        await _service(db_session, settings, email).send(lead_id, final_attempt=False)
    reply = await _reply(db_session, lead_id)
    assert (reply.delivery_status, reply.send_started_at) == (DeliveryStatus.QUEUED, None)

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SENT
    )
    assert len(email.sent) == 1


async def test_an_unknown_outcome_is_never_resent(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id))
    email = FakeEmailSender(SendError("SMTP outcome unknown", retryable=False))

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.FAILED
    )
    reply = await _reply(db_session, lead_id)
    assert reply.delivery_status == DeliveryStatus.FAILED
    assert reply.delivery_error == "SMTP outcome unknown"
    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SKIPPED
    )
    assert email.sent == []


async def test_a_send_cut_off_by_a_crash_is_not_repeated(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id))
    # The worker died after marking the send as started.
    with pytest.raises(RuntimeError):
        await _service(db_session, settings, FakeEmailSender(RuntimeError("killed"))).send(
            lead_id, final_attempt=False
        )
    reply = await _reply(db_session, lead_id)
    assert reply.delivery_status == DeliveryStatus.FAILED

    await db_session.execute(
        update(Message)
        .where(Message.id == reply.id)
        .values(delivery_status=DeliveryStatus.QUEUED, send_started_at=datetime.now(UTC))
    )
    await db_session.commit()
    email = FakeEmailSender()

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.UNKNOWN
    )
    assert email.sent == []
    assert (await _reply(db_session, lead_id)).delivery_status == DeliveryStatus.FAILED


async def test_the_last_attempt_gives_up_with_the_error_recorded(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id))
    email = FakeEmailSender(SendError("451 try later", retryable=True))

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=True) == (
        AutoReplyOutcome.FAILED
    )
    assert (await _reply(db_session, lead_id)).delivery_error == "451 try later"


async def test_oauth_mailboxes_fail_clearly(db_session: AsyncSession, settings: Settings) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    await db_session.execute(
        update(Mailbox)
        .where(Mailbox.id == mailbox_id)
        .values(auth_type=MailboxAuthType.OAUTH2_XOAUTH2)
    )
    await db_session.commit()
    lead_id = await _scored_lead(db_session, _email(mailbox_id))

    assert await _service(db_session, settings).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.FAILED
    )
    assert "OAuth2" in ((await _reply(db_session, lead_id)).delivery_error or "")


async def test_whatsapp_reply_quotes_the_message_in_the_lead_language(
    db_session: AsyncSession, settings: Settings
) -> None:
    inbound = _whatsapp()
    lead_id = await _scored_lead(db_session, inbound, language="ar")
    graph = FakeGraph()

    assert await _service(db_session, settings, graph=graph).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SENT
    )

    [request] = graph.requests
    assert request["to"] == "971501234567"
    assert request["context"] == {"message_id": inbound.external_id}
    assert str(request["text"]["body"]).startswith("شكرًا")  # type: ignore[index]
    reply = await _reply(db_session, lead_id)
    assert reply.external_id == "wamid.OUT1"
    assert reply.delivery_status == DeliveryStatus.SENT


async def test_whatsapp_leads_outside_the_24_hour_window_are_skipped(
    db_session: AsyncSession, settings: Settings
) -> None:
    lead_id = await _scored_lead(
        db_session, _whatsapp(occurred_at=datetime.now(UTC) - timedelta(hours=25))
    )
    graph = FakeGraph()

    assert await _service(db_session, settings, graph=graph).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SKIPPED
    )
    assert graph.requests == []


async def test_delivery_receipts_only_move_forward(
    db_session: AsyncSession, settings: Settings
) -> None:
    lead_id = await _scored_lead(db_session, _whatsapp())
    await _service(db_session, settings, graph=FakeGraph()).send(lead_id, final_attempt=False)

    async def receive(*statuses: tuple[str, str, dict[str, object] | None]) -> None:
        inbox = WhatsAppInbox(db_session, settings)
        await inbox.process_webhook(await inbox.accept(wa_status_payload(*statuses)))

    await receive(("wamid.OUT1", "read", None))
    await receive(("wamid.OUT1", "delivered", None))  # late, out of order
    await receive(("wamid.OUT1", "failed", {"code": 1, "title": "late"}))
    await receive(("wamid.UNKNOWN", "delivered", None))

    reply = await _reply(db_session, lead_id)
    assert reply.delivery_status == DeliveryStatus.READ
    assert reply.delivery_error is None


async def test_a_failed_receipt_is_recorded(db_session: AsyncSession, settings: Settings) -> None:
    lead_id = await _scored_lead(db_session, _whatsapp())
    await _service(db_session, settings, graph=FakeGraph()).send(lead_id, final_attempt=False)
    inbox = WhatsAppInbox(db_session, settings)

    payload = wa_status_payload(
        ("wamid.OUT1", "failed", {"code": 131026, "title": "Undeliverable"})
    )
    await inbox.process_webhook(await inbox.accept(payload))

    reply = await _reply(db_session, lead_id)
    assert reply.delivery_status == DeliveryStatus.FAILED
    assert reply.delivery_error == "131026 Undeliverable"


async def test_rescoring_does_not_queue_a_second_reply(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    first = _email(mailbox_id)
    lead_id = await _scored_lead(db_session, first)
    await _service(db_session, settings).send(lead_id, final_attempt=False)
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()

    await _scored_lead(db_session, _email(mailbox_id, thread_refs=(first.external_id,)))

    assert await queued_jobs(db_session, names.SEND_AUTO_REPLY) == []


async def test_a_long_thread_keeps_its_order(db_session: AsyncSession, settings: Settings) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    inbound = _email(mailbox_id, thread_refs=("<c@x>", "<b@x>", "<a@x>"))  # as the parser stores
    lead_id = await _scored_lead(db_session, inbound)
    email = FakeEmailSender()

    await _service(db_session, settings, email).send(lead_id, final_attempt=False)

    [(message, _)] = email.sent
    assert message["References"] == f"<a@x> <b@x> <c@x> {inbound.external_id}"


async def test_a_closed_lead_gets_no_reply(db_session: AsyncSession, settings: Settings) -> None:
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id))
    await db_session.execute(update(Lead).where(Lead.id == lead_id).values(status=LeadStatus.LOST))
    await db_session.commit()
    email = FakeEmailSender()

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SKIPPED
    )
    assert email.sent == []


async def test_a_reply_already_claimed_by_another_run_is_left_alone(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two runs both found the row unsent; the other one claimed it first.
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id))
    original = MessageRepository.claim_send

    async def claimed_elsewhere(self: MessageRepository, message_id: uuid.UUID) -> bool:
        await original(self, message_id)  # the other run's claim
        return await original(self, message_id)  # ours: too late

    monkeypatch.setattr(MessageRepository, "claim_send", claimed_elsewhere)
    email = FakeEmailSender()

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SKIPPED
    )
    assert email.sent == []
    reply = await _reply(db_session, lead_id)
    assert reply.delivery_status == DeliveryStatus.QUEUED  # the other run reports the outcome


async def test_a_receipt_before_the_send_commits_is_rechecked(
    db_session: AsyncSession, settings: Settings
) -> None:
    inbox = WhatsAppInbox(db_session, settings)
    payload = wa_status_payload(
        ("wamid.OUT1", "failed", {"code": 131026, "title": "Undeliverable"})
    )
    await inbox.process_webhook(await inbox.accept(payload))  # no message has this wamid yet

    [job] = await queued_jobs(db_session, names.RECHECK_DELIVERY_RECEIPTS)
    lead_id = await _scored_lead(db_session, _whatsapp())
    await _service(db_session, settings, graph=FakeGraph()).send(lead_id, final_attempt=False)

    receipts = [
        DeliveryUpdate(Channel.WHATSAPP, r["wamid"], DeliveryStatus(r["status"]), r["error"])
        for r in job["args"]["receipts"]
    ]
    dropped = await inbox.apply_receipts(receipts, recheck_unmatched=False)

    assert dropped == 0
    reply = await _reply(db_session, lead_id)
    assert reply.delivery_status == DeliveryStatus.FAILED
    assert reply.delivery_error == "131026 Undeliverable"


async def test_a_later_delivery_clears_an_earlier_error(
    db_session: AsyncSession, settings: Settings
) -> None:
    lead_id = await _scored_lead(db_session, _whatsapp())
    await _service(db_session, settings, graph=FakeGraph()).send(lead_id, final_attempt=False)
    inbox = WhatsAppInbox(db_session, settings)

    for status in (
        ("wamid.OUT1", "failed", {"code": 1, "title": "x"}),
        ("wamid.OUT1", "read", None),
    ):
        await inbox.process_webhook(await inbox.accept(wa_status_payload(status)))

    reply = await _reply(db_session, lead_id)
    assert (reply.delivery_status, reply.delivery_error) == (DeliveryStatus.READ, None)


async def test_no_acknowledgement_after_a_person_has_answered(
    db_session: AsyncSession, settings: Settings
) -> None:
    # Regression: a rep replied before the scoring job ran; the customer then got "we'll be
    # in touch" after the human answer.
    mailbox_id = await _mailbox(db_session, settings)
    lead_id = await _scored_lead(db_session, _email(mailbox_id))
    await db_session.execute(
        update(Lead).where(Lead.id == lead_id).values(first_response_at=datetime.now(UTC))
    )
    await db_session.commit()
    email = FakeEmailSender()

    assert await _service(db_session, settings, email).send(lead_id, final_attempt=False) == (
        AutoReplyOutcome.SKIPPED
    )
    assert email.sent == []
