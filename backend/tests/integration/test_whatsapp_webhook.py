import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.integrations.openai_audio import TranscriptionError
from app.integrations.whatsapp import GRAPH_HOST, WhatsAppClient
from app.main import create_app
from app.models import InboundWebhook, Lead, Message
from app.models.enums import MessageKind, TranscriptionStatus
from app.services.ingestion import Outcome
from app.services.whatsapp_inbox import TRANSCRIPT_UNAVAILABLE, VoiceNotes, WhatsAppInbox
from app.workers import names
from tests.fakes.messages import PHONE_NUMBER_ID, wa_text, wa_voice, whatsapp_payload
from tests.integration.conftest import queued_jobs

pytestmark = pytest.mark.integration

APP_SECRET = "meta-app-secret"
VERIFY_TOKEN = "verify-me"
URL = "/api/v1/webhooks/whatsapp"


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "whatsapp_app_secret": SecretStr(APP_SECRET),
            "whatsapp_verify_token": SecretStr(VERIFY_TOKEN),
            "whatsapp_phone_number_id": PHONE_NUMBER_ID,
        }
    )


@pytest.fixture
async def meta(db_session: AsyncSession, settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


def _signed(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": signature, "Content-Type": "application/json"}


async def test_subscription_challenge_is_echoed_for_the_right_token(meta: AsyncClient) -> None:
    ok = await meta.get(
        URL,
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "42"},
    )
    bad = await meta.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}
    )

    assert (ok.status_code, ok.text) == (200, "42")
    assert bad.status_code == 403


async def test_unsigned_or_forged_deliveries_are_rejected(
    meta: AsyncClient, db_session: AsyncSession
) -> None:
    body, _ = _signed(whatsapp_payload(wa_text("hi")))

    unsigned = await meta.post(URL, content=body)
    forged = await meta.post(URL, content=body, headers={"X-Hub-Signature-256": "sha256=00"})

    assert (unsigned.status_code, forged.status_code) == (401, 401)
    assert await db_session.scalar(select(InboundWebhook)) is None


async def test_without_an_app_secret_the_webhook_fails_closed(
    db_session: AsyncSession, settings: Settings
) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"whatsapp_app_secret": None}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        body, headers = _signed(whatsapp_payload(wa_text("hi")))
        response = await c.post(URL, content=body, headers=headers)

    assert response.status_code == 503


async def test_a_signed_delivery_is_stored_and_queued_then_becomes_a_lead(
    meta: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    body, headers = _signed(whatsapp_payload(wa_text("Need a quote for 3 branches")))

    response = await meta.post(URL, content=body, headers=headers)

    assert response.status_code == 200
    webhook = await db_session.scalar(select(InboundWebhook))
    assert webhook is not None
    assert await queued_jobs(db_session, names.PROCESS_WHATSAPP_WEBHOOK) == [
        {"task": names.PROCESS_WHATSAPP_WEBHOOK, "args": {"webhook_id": str(webhook.id)}}
    ]

    # What the worker then does with it, twice (Meta retries, jobs can re-run).
    inbox = WhatsAppInbox(db_session, settings)
    assert await inbox.process_webhook(webhook.id) == {Outcome.NEW_LEAD: 1}
    assert await inbox.process_webhook(webhook.id) == {Outcome.DUPLICATE: 1}
    lead = await db_session.scalar(select(Lead))
    assert lead is not None
    await db_session.refresh(webhook)
    assert webhook.processed_at is not None


async def test_signed_but_unparseable_bodies_are_acknowledged(
    meta: AsyncClient, db_session: AsyncSession
) -> None:
    body = b"not json"
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

    response = await meta.post(URL, content=body, headers={"X-Hub-Signature-256": signature})

    assert response.status_code == 200  # Meta would retry a non-200 forever
    assert await db_session.scalar(select(InboundWebhook)) is None


class _FakeTranscriber:
    def __init__(self, text: str = "", error: TranscriptionError | None = None) -> None:
        self.text, self.error, self.calls = text, error, 0

    async def transcribe(self, audio: bytes, *, mime_type: str) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        assert audio == b"OggS-voice-bytes"
        return self.text


def _graph(*, media_status: int = 200) -> WhatsAppClient:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        if request.url.path == "/v26.0/MEDIA1":
            if media_status != 200:
                return httpx.Response(media_status, json={"error": {"code": 100}})
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.fbsbx.com/media/abc",
                    "mime_type": "audio/ogg",
                    "file_size": 16,
                },
            )
        if request.url.host == "lookaside.fbsbx.com":
            return httpx.Response(200, content=b"OggS-voice-bytes")
        return httpx.Response(404)

    assert GRAPH_HOST == "https://graph.facebook.com"
    return WhatsAppClient(
        access_token="token", graph_version="v26.0", transport=httpx.MockTransport(handle)
    )


async def _stored_voice_note(db_session: AsyncSession, settings: Settings) -> Message:
    db_session.add(InboundWebhook(source="whatsapp", payload=whatsapp_payload(wa_voice())))
    await db_session.flush()
    webhook = await db_session.scalar(select(InboundWebhook))
    assert webhook is not None
    await WhatsAppInbox(db_session, settings).process_webhook(webhook.id)
    message: Message | None = await db_session.scalar(select(Message))
    assert message is not None
    assert message.kind == MessageKind.VOICE
    return message


async def test_a_voice_note_is_transcribed_then_sent_on(
    db_session: AsyncSession, settings: Settings
) -> None:
    message = await _stored_voice_note(db_session, settings)
    transcriber = _FakeTranscriber("أريد عرض سعر لثلاثة فروع")  # "I want a quote for 3 branches"

    await VoiceNotes(db_session, _graph(), transcriber).transcribe(message.id, final_attempt=False)

    await db_session.refresh(message)
    assert message.transcription_status == TranscriptionStatus.DONE
    assert message.body_text == "أريد عرض سعر لثلاثة فروع"
    assert [j["task"] for j in await queued_jobs(db_session, names.PROCESS_LEAD)] == [
        names.PROCESS_LEAD
    ]
    # A retried job after success does nothing.
    await VoiceNotes(db_session, _graph(), transcriber).transcribe(message.id, final_attempt=False)
    assert transcriber.calls == 1


async def test_a_transient_failure_is_retried_until_the_last_attempt(
    db_session: AsyncSession, settings: Settings
) -> None:
    message = await _stored_voice_note(db_session, settings)
    flaky = _FakeTranscriber(error=TranscriptionError("rate limited", retryable=True))

    with pytest.raises(TranscriptionError):
        await VoiceNotes(db_session, _graph(), flaky).transcribe(message.id, final_attempt=False)
    await VoiceNotes(db_session, _graph(), flaky).transcribe(message.id, final_attempt=True)

    await db_session.refresh(message)
    assert message.transcription_status == TranscriptionStatus.FAILED
    assert message.body_text == TRANSCRIPT_UNAVAILABLE
    assert len(await queued_jobs(db_session, names.PROCESS_LEAD)) == 1


@pytest.mark.parametrize("setup", ["expired_media", "not_configured"])
async def test_permanent_failures_do_not_retry(
    db_session: AsyncSession, settings: Settings, setup: str
) -> None:
    message = await _stored_voice_note(db_session, settings)
    graph = _graph(media_status=404) if setup == "expired_media" else None

    await VoiceNotes(db_session, graph, _FakeTranscriber("x")).transcribe(
        message.id, final_attempt=False
    )

    await db_session.refresh(message)
    assert message.transcription_status == TranscriptionStatus.FAILED
    assert len(await queued_jobs(db_session, names.PROCESS_LEAD)) == 1


async def test_unknown_webhook_ids_are_ignored(
    db_session: AsyncSession, settings: Settings
) -> None:
    assert await WhatsAppInbox(db_session, settings).process_webhook(uuid.uuid4()) == {}
