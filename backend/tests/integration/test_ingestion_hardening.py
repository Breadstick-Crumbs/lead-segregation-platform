"""Hostile or failing inputs: none may block a queue or leave a lead stuck."""

import hashlib
import hmac
from collections.abc import AsyncIterator

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.integrations.imap import FetchedEmail
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.main import create_app
from app.models import Message
from app.models.enums import TranscriptionStatus
from app.schemas.inbound import InboundMessage
from app.schemas.mailbox import MailboxCreate
from app.services.ingestion import IngestionResult, IngestionService, Outcome
from app.services.mailboxes import MailboxService
from app.services.whatsapp_inbox import TRANSCRIPT_UNAVAILABLE, VoiceNotes, WhatsAppInbox
from app.workers import names
from app.workers.queue import enqueue
from tests.fakes.messages import PHONE_NUMBER_ID, raw_email, wa_voice, whatsapp_payload
from tests.integration.conftest import queued_jobs
from tests.integration.test_mailboxes import NEW_MAILBOX, FakeReader

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "secrets_encryption_key": SecretStr(Fernet.generate_key().decode()),
            "whatsapp_app_secret": SecretStr("meta-app-secret"),
            "whatsapp_phone_number_id": PHONE_NUMBER_ID,
        }
    )


async def test_hostile_email_headers_are_stored_safely(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox = await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    hostile = raw_email(
        sender=f'"{"A" * 300}" <x@attacker.test>',
        message_id=f"<{'m' * 900}@attacker.test>",
        body="NUL \x00 inside",
    )
    reader = FakeReader(emails=[FetchedEmail(1, hostile)])

    counts = await MailboxService(db_session, settings).poll(mailbox.id, reader)

    assert counts == {Outcome.NEW_LEAD: 1}
    stored = await db_session.scalar(select(Message))
    assert stored is not None
    assert stored.external_id is not None
    assert stored.external_id.startswith("<sha256.")
    assert "\x00" not in stored.body_text


async def test_a_message_that_fails_to_store_is_skipped_not_blocking(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox = await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    original = IngestionService.ingest

    async def flaky(self: IngestionService, message: InboundMessage) -> IngestionResult:
        if message.external_id == "<poison@x>":
            raise RuntimeError("database said no")
        return await original(self, message)

    monkeypatch.setattr(IngestionService, "ingest", flaky)
    reader = FakeReader(
        emails=[
            FetchedEmail(1, raw_email(message_id="<poison@x>")),
            FetchedEmail(2, raw_email(message_id="<fine@gulfbuild.ae>")),
        ]
    )

    counts = await MailboxService(db_session, settings).poll(mailbox.id, reader)

    assert counts == {Outcome.NEW_LEAD: 1}
    await db_session.refresh(mailbox)
    assert mailbox.last_uid == 2  # moved past the poison message
    assert mailbox.last_error == "Skipped UID 1: RuntimeError"


async def _voice_message(db_session: AsyncSession, settings: Settings) -> Message:
    webhook_id = await WhatsAppInbox(db_session, settings).accept(whatsapp_payload(wa_voice()))
    await WhatsAppInbox(db_session, settings).process_webhook(webhook_id)
    message: Message | None = await db_session.scalar(select(Message))
    assert message is not None
    return message


def _timing_out_graph() -> WhatsAppClient:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("graph.facebook.com timed out", request=request)

    return WhatsAppClient(
        access_token="t", graph_version="v26.0", transport=httpx.MockTransport(handle)
    )


class _NeverCalled:
    async def transcribe(self, audio: bytes, *, mime_type: str) -> str:
        raise AssertionError("download should have failed first")


async def test_a_network_timeout_retries_then_gives_up_cleanly(
    db_session: AsyncSession, settings: Settings
) -> None:
    message = await _voice_message(db_session, settings)
    notes = VoiceNotes(db_session, _timing_out_graph(), _NeverCalled())

    with pytest.raises(WhatsAppError) as raised:
        await notes.transcribe(message.id, final_attempt=False)
    assert raised.value.retryable
    await notes.transcribe(message.id, final_attempt=True)

    await db_session.refresh(message)
    assert message.transcription_status == TranscriptionStatus.FAILED
    assert message.body_text == TRANSCRIPT_UNAVAILABLE
    assert len(await queued_jobs(db_session, names.PROCESS_LEAD)) == 1


async def test_the_transcript_is_saved_even_if_processing_is_already_queued(
    db_session: AsyncSession, settings: Settings
) -> None:
    message = await _voice_message(db_session, settings)
    await enqueue(
        db_session,
        names.PROCESS_LEAD,
        queueing_lock=f"process_lead:{message.lead_id}",
        lead_id=str(message.lead_id),
    )
    await db_session.commit()

    await VoiceNotes(db_session, _timing_out_graph(), _NeverCalled()).transcribe(
        message.id, final_attempt=True
    )

    await db_session.refresh(message)
    assert message.transcription_status == TranscriptionStatus.FAILED
    assert len(await queued_jobs(db_session, names.PROCESS_LEAD)) == 1


@pytest.fixture
async def meta(db_session: AsyncSession, settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


async def test_an_oversized_delivery_is_refused_before_reading(meta: AsyncClient) -> None:
    response = await meta.post(
        "/api/v1/webhooks/whatsapp",
        content=b"{}",
        headers={"Content-Length": str(50 * 1024 * 1024), "X-Hub-Signature-256": "sha256=00"},
    )

    assert response.status_code == 413


async def test_an_oversized_streamed_body_is_refused(meta: AsyncClient) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(4):
            yield b"x" * (1024 * 1024)

    response = await meta.post("/api/v1/webhooks/whatsapp", content=chunks())

    assert response.status_code == 413


async def test_a_garbage_signature_header_is_a_401(meta: AsyncClient) -> None:
    body = b"{}"
    good = "sha256=" + hmac.new(b"meta-app-secret", body, hashlib.sha256).hexdigest()

    bad = await meta.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers=[(b"X-Hub-Signature-256", "sha256=éé".encode("latin-1"))],
    )
    ok = await meta.post(
        "/api/v1/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": good}
    )

    assert bad.status_code == 401
    assert ok.status_code == 200
