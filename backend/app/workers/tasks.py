"""Job handlers. Each opens its own session; the logic lives in the services."""

import uuid
from datetime import UTC, datetime, timedelta

from procrastinate import JobContext, RetryStrategy
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.integrations.imap import ImapToolsReader
from app.integrations.openai_audio import OpenAITranscriber, Transcriber, TranscriptionError
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.models import InboundWebhook
from app.services.mailboxes import MailboxService, enqueue_poll
from app.services.whatsapp_inbox import VoiceNotes, WhatsAppInbox
from app.workers import names
from app.workers.app import app

logger = get_logger(__name__)

# procrastinate's max_attempts counts *retries*: N retries means N + 1 runs.
TRANSCRIBE_RUNS = 5
RETRY = RetryStrategy(max_attempts=4, exponential_wait=5)  # 5 runs: waits ~5s, 25s, 2m, 10m
KEEP_SUCCEEDED_JOBS_HOURS = 7 * 24
KEEP_PROCESSED_WEBHOOKS = timedelta(days=30)


@app.task(name=names.PROCESS_WHATSAPP_WEBHOOK, retry=RETRY)
async def process_whatsapp_webhook(webhook_id: str) -> None:
    async with SessionFactory() as session:
        inbox = WhatsAppInbox(session, get_settings())
        try:
            counts = await inbox.process_webhook(uuid.UUID(webhook_id))
        except Exception as exc:
            await inbox.record_webhook_error(uuid.UUID(webhook_id), repr(exc))
            raise
    logger.info("whatsapp_webhook_processed", webhook_id=webhook_id, **counts)


@app.task(
    name=names.TRANSCRIBE_MESSAGE,
    retry=RetryStrategy(max_attempts=TRANSCRIBE_RUNS - 1, exponential_wait=5),
    pass_context=True,
)
async def transcribe_message(context: JobContext, message_id: str) -> None:
    settings = get_settings()
    whatsapp: WhatsAppClient | None
    transcriber: Transcriber | None
    try:
        whatsapp = WhatsAppClient.from_settings(settings)
    except WhatsAppError:
        whatsapp = None
    try:
        transcriber = OpenAITranscriber(settings)
    except TranscriptionError:
        transcriber = None
    # `attempts` counts earlier runs (0 on the first), so the last run sees RUNS - 1; it must
    # not leave the lead stuck.
    final_attempt = context.job.attempts >= TRANSCRIBE_RUNS - 1
    async with SessionFactory() as session:
        await VoiceNotes(session, whatsapp, transcriber).transcribe(
            uuid.UUID(message_id), final_attempt=final_attempt
        )


@app.task(name=names.PROCESS_LEAD, retry=RETRY)
async def process_lead(lead_id: str) -> None:
    """Classification, scoring and auto-reply arrive in Phase 3/4. Until then the lead stays
    `pending`, ready for the pipeline."""
    logger.info("process_lead_awaiting_ai_pipeline", lead_id=lead_id)


@app.task(name=names.POLL_MAILBOX, retry=RetryStrategy(max_attempts=1, wait=30))
async def poll_mailbox(mailbox_id: str) -> None:
    async with SessionFactory() as session:
        counts = await MailboxService(session, get_settings()).poll(
            uuid.UUID(mailbox_id), ImapToolsReader()
        )
    logger.info("poll_mailbox_done", mailbox_id=mailbox_id, **counts)


@app.periodic(cron="* * * * *")
@app.task(name=names.POLL_ALL_MAILBOXES)
async def poll_all_mailboxes(timestamp: int) -> None:
    """Every minute, queue a poll per active mailbox (skipped while one is still waiting)."""
    async with SessionFactory() as session:
        for mailbox_id in await MailboxService(session, get_settings()).active_ids():
            await enqueue_poll(session, mailbox_id)
        await session.commit()


@app.periodic(cron="17 3 * * *")
@app.task(name=names.PRUNE_HISTORY)
async def prune_history(timestamp: int) -> None:
    """Nightly: drop finished jobs and processed webhook payloads past their retention.
    Failed jobs and unprocessed payloads are kept for investigation."""
    await app.job_manager.delete_old_jobs(nb_hours=KEEP_SUCCEEDED_JOBS_HOURS)
    cutoff = datetime.now(UTC) - KEEP_PROCESSED_WEBHOOKS
    async with SessionFactory() as session:
        result = await session.execute(
            delete(InboundWebhook).where(
                InboundWebhook.processed_at.is_not(None), InboundWebhook.processed_at < cutoff
            )
        )
        await session.commit()
    logger.info("history_pruned", webhooks=result.rowcount)  # type: ignore[attr-defined]
