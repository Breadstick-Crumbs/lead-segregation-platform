"""Background processing of stored WhatsApp webhooks and voice notes."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.openai_audio import Transcriber, TranscriptionError
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError, parse_webhook
from app.models import InboundWebhook, Message
from app.models.enums import MessageKind, TranscriptionStatus, WebhookSource
from app.services.ingestion import IngestionService, Outcome
from app.workers import names
from app.workers.queue import enqueue

logger = get_logger(__name__)

TRANSCRIPT_UNAVAILABLE = "[Voice note: transcription unavailable]"


class WhatsAppInbox:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def accept(self, payload: dict[str, Any]) -> uuid.UUID:
        """Store a verified delivery and queue its processing, in one transaction."""
        webhook = InboundWebhook(source=WebhookSource.WHATSAPP, payload=payload)
        self._session.add(webhook)
        await self._session.flush()
        await enqueue(self._session, names.PROCESS_WHATSAPP_WEBHOOK, webhook_id=str(webhook.id))
        await self._session.commit()
        return webhook.id

    async def process_webhook(self, webhook_id: uuid.UUID) -> dict[Outcome, int]:
        """Ingest every message in a stored payload. Safe to re-run: ingestion dedupes."""
        webhook = await self._session.get(InboundWebhook, webhook_id)
        if webhook is None:
            logger.warning("webhook_missing", webhook_id=str(webhook_id))
            return {}
        payload = webhook.payload
        counts: dict[Outcome, int] = {}
        ingestion = IngestionService(self._session)
        for message in parse_webhook(
            payload, phone_number_id=self._settings.whatsapp_phone_number_id
        ):
            result = await ingestion.ingest(message)  # commits per message
            counts[result.outcome] = counts.get(result.outcome, 0) + 1

        webhook = await self._session.get(InboundWebhook, webhook_id)
        if webhook is not None:
            webhook.processed_at = datetime.now(UTC)
            webhook.error = None
            await self._session.commit()
        return counts

    async def record_webhook_error(self, webhook_id: uuid.UUID, error: str) -> None:
        await self._session.rollback()
        webhook = await self._session.get(InboundWebhook, webhook_id)
        if webhook is not None:
            webhook.error = error[:2000]
            await self._session.commit()


class VoiceNotes:
    def __init__(
        self,
        session: AsyncSession,
        whatsapp: WhatsAppClient | None,
        transcriber: Transcriber | None,
    ) -> None:
        """`whatsapp`/`transcriber` are None when not configured: the note is then marked
        failed straight away (retrying can't help) and the lead moves on."""
        self._session = session
        self._whatsapp = whatsapp
        self._transcriber = transcriber

    async def transcribe(self, message_id: uuid.UUID, *, final_attempt: bool) -> None:
        message = await self._session.get(Message, message_id)
        if (
            message is None
            or message.kind != MessageKind.VOICE
            or message.transcription_status != TranscriptionStatus.PENDING
            or message.media_id is None
        ):
            return  # already handled (job retried after success) or nothing to do

        try:
            if self._whatsapp is None or self._transcriber is None:
                raise TranscriptionError("WhatsApp or OpenAI is not configured", retryable=False)
            audio, mime = await self._whatsapp.download_media(message.media_id)
            transcript = await self._transcriber.transcribe(
                audio, mime_type=message.media_mime_type or mime
            )
        except (WhatsAppError, TranscriptionError) as exc:
            if exc.retryable and not final_attempt:
                logger.warning("transcription_retry", message_id=str(message_id), error=str(exc))
                raise
            logger.error("transcription_failed", message_id=str(message_id), error=str(exc))
            await self._finish(message, TRANSCRIPT_UNAVAILABLE, TranscriptionStatus.FAILED)
            return
        except Exception:
            # Unexpected error: retry while we can, but never leave the lead stuck pending.
            if not final_attempt:
                raise
            logger.exception("transcription_failed_unexpectedly", message_id=str(message_id))
            await self._finish(message, TRANSCRIPT_UNAVAILABLE, TranscriptionStatus.FAILED)
            return

        await self._finish(message, transcript or TRANSCRIPT_UNAVAILABLE, TranscriptionStatus.DONE)
        logger.info("transcription_done", message_id=str(message_id), chars=len(transcript))

    async def _finish(self, message: Message, text: str, status: TranscriptionStatus) -> None:
        message.body_text = text
        message.transcription_status = status
        await enqueue(
            self._session,
            names.PROCESS_LEAD,
            queueing_lock=f"process_lead:{message.lead_id}",
            lead_id=str(message.lead_id),
        )
        await self._session.commit()
