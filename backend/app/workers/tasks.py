"""Job handlers. Each opens its own session; the logic lives in the services."""

import uuid
from datetime import UTC, datetime, timedelta

from procrastinate import JobContext, RetryStrategy
from procrastinate.jobs import Status
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.integrations.chat_webhooks import ChatNotifier, SlackNotifier, TeamsNotifier
from app.integrations.imap import ImapToolsReader
from app.integrations.openai_audio import OpenAITranscriber, Transcriber, TranscriptionError
from app.integrations.openai_llm import (
    AIError,
    Embedder,
    LeadAnalyzer,
    OpenAIEmbedder,
    OpenAILeadAnalyzer,
)
from app.integrations.smtp import AioSmtpSender
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.models import InboundWebhook
from app.models.enums import Channel, DeliveryStatus, NotificationKind
from app.repositories.leads import DeliveryUpdate
from app.services.auto_reply import AutoReplyService, enqueue_auto_reply
from app.services.lead_pipeline import LeadPipeline
from app.services.lead_queue import sweep_pending_leads
from app.services.mailboxes import MailboxService, enqueue_poll
from app.services.notifications import NotificationService
from app.services.whatsapp_inbox import VoiceNotes, WhatsAppInbox
from app.workers import names
from app.workers.app import app

logger = get_logger(__name__)

# procrastinate's max_attempts counts *retries*: N retries means N + 1 runs.
TRANSCRIBE_RUNS = 5
PROCESS_LEAD_RUNS = 5
AUTO_REPLY_RUNS = 5
ALERT_RUNS = 5
STALLED_AFTER_SECONDS = 300  # no worker heartbeat for 5 minutes
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


@app.task(
    name=names.PROCESS_LEAD,
    retry=RetryStrategy(max_attempts=PROCESS_LEAD_RUNS - 1, exponential_wait=5),
    pass_context=True,
)
async def process_lead(context: JobContext, lead_id: str) -> None:
    settings = get_settings()
    analyzer: LeadAnalyzer | None
    embedder: Embedder | None
    try:
        analyzer, embedder = OpenAILeadAnalyzer(settings), OpenAIEmbedder(settings)
    except AIError:
        analyzer = embedder = None
    final_attempt = context.job.attempts >= PROCESS_LEAD_RUNS - 1
    async with SessionFactory() as session:
        outcome = await LeadPipeline(session, analyzer, embedder).process(
            uuid.UUID(lead_id), final_attempt=final_attempt
        )
    logger.info("process_lead_done", lead_id=lead_id, outcome=outcome)


@app.periodic(cron="*/5 * * * *")
@app.task(name=names.SWEEP_PENDING_LEADS)
async def sweep_pending(timestamp: int) -> None:
    """Every 5 minutes, re-queue leads left pending without a job (for example, ingested
    before the pipeline existed, or after a job was deleted)."""
    # A worker that died mid-run leaves its job `doing` forever, and the sweep skips leads
    # with a running job. Jobs whose worker stopped sending heartbeats are marked failed, so
    # their leads are picked up below.
    for task_name in (names.PROCESS_LEAD, names.SEND_AUTO_REPLY):
        for job in await app.job_manager.get_stalled_jobs(
            task_name=task_name, seconds_since_heartbeat=STALLED_AFTER_SECONDS
        ):
            if task_name == names.SEND_AUTO_REPLY:
                # Queue the follow-up *before* failing the stalled job, so a crash in
                # between can't lose it (the job's lock holds it until the old one ends).
                # It resolves the reply: unsent → sends; cut off → "outcome unknown".
                async with SessionFactory() as session:
                    await enqueue_auto_reply(session, uuid.UUID(str(job.task_kwargs["lead_id"])))
                    await session.commit()
            await app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
            logger.warning("stalled_job_failed", task=task_name, job_id=job.id)
    async with SessionFactory() as session:
        queued = await sweep_pending_leads(session)
    if queued:
        logger.info("pending_leads_requeued", count=queued)


@app.task(
    name=names.SEND_AUTO_REPLY,
    retry=RetryStrategy(max_attempts=AUTO_REPLY_RUNS - 1, exponential_wait=5),
    pass_context=True,
)
async def send_auto_reply(context: JobContext, lead_id: str) -> None:
    settings = get_settings()
    try:
        whatsapp: WhatsAppClient | None = WhatsAppClient.from_settings(settings)
    except WhatsAppError:
        whatsapp = None
    final_attempt = context.job.attempts >= AUTO_REPLY_RUNS - 1
    async with SessionFactory() as session:
        outcome = await AutoReplyService(
            session, settings, email=AioSmtpSender(), whatsapp=whatsapp
        ).send(uuid.UUID(lead_id), final_attempt=final_attempt)
    logger.info("send_auto_reply_done", lead_id=lead_id, outcome=outcome)


@app.task(name=names.RECHECK_DELIVERY_RECEIPTS)
async def recheck_delivery_receipts(receipts: list[dict[str, str | None]]) -> None:
    updates = [
        DeliveryUpdate(
            Channel.WHATSAPP, str(r["wamid"]), DeliveryStatus(str(r["status"])), r.get("error")
        )
        for r in receipts
    ]
    async with SessionFactory() as session:
        dropped = await WhatsAppInbox(session, get_settings()).apply_receipts(
            updates, recheck_unmatched=False
        )
    if dropped:
        logger.info("delivery_receipts_unmatched", count=dropped)


@app.task(name=names.NOTIFY_LEAD, retry=RETRY)
async def notify_lead(lead_id: str, kind: str) -> None:
    async with SessionFactory() as session:
        raised = await NotificationService(session, get_settings()).raise_alert(
            uuid.UUID(lead_id), NotificationKind(kind)
        )
    logger.info("notify_lead_done", lead_id=lead_id, kind=kind, raised=raised)


def _chat_notifiers() -> dict[str, ChatNotifier]:
    settings = get_settings()
    notifiers: dict[str, ChatNotifier] = {}
    if settings.slack_webhook_url is not None:
        notifiers["slack"] = SlackNotifier(settings.slack_webhook_url.get_secret_value())
    if settings.teams_webhook_url is not None:
        notifiers["teams"] = TeamsNotifier(settings.teams_webhook_url.get_secret_value())
    return notifiers


@app.task(
    name=names.DELIVER_ALERT,
    retry=RetryStrategy(max_attempts=ALERT_RUNS - 1, exponential_wait=5),
    pass_context=True,
)
async def deliver_alert(
    context: JobContext, lead_id: str, kind: str, channel: str, target: str | None
) -> None:
    async with SessionFactory() as session:
        await NotificationService(session, get_settings()).deliver(
            uuid.UUID(lead_id),
            NotificationKind(kind),
            channel,
            target,
            email=AioSmtpSender(),
            chat=_chat_notifiers(),
            final_attempt=context.job.attempts >= ALERT_RUNS - 1,
        )


@app.periodic(cron="* * * * *")
@app.task(name=names.SWEEP_SLA)
async def sweep_sla(timestamp: int) -> None:
    """Every minute: escalate high and critical leads nobody has answered within the SLA."""
    async with SessionFactory() as session:
        queued = await NotificationService(session, get_settings()).sweep_sla()
    if queued:
        logger.info("sla_breaches_escalated", count=queued)


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
