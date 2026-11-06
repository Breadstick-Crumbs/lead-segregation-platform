"""Mailbox administration and polling."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import anyio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import SecretBox
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.integrations.email_parser import parse_email
from app.integrations.imap import FetchedEmail, ImapCredentials, MailboxReader
from app.models import Mailbox
from app.models.enums import MailboxAuthType
from app.repositories.mailboxes import MailboxRepository
from app.schemas.app_settings import IngestionSettings
from app.schemas.mailbox import MailboxCreate, MailboxUpdate
from app.services.app_settings import load_settings
from app.services.ingestion import IngestionService, Outcome
from app.workers import names
from app.workers.queue import enqueue

logger = get_logger(__name__)

# Full messages (with attachments) are held in memory, so keep batches small.
POLL_BATCH_SIZE = 20


async def enqueue_poll(session: AsyncSession, mailbox_id: uuid.UUID) -> bool:
    """Queue a poll. `queueing_lock` drops it if one is already waiting; `lock` keeps two
    workers from polling the same mailbox at once."""
    return await enqueue(
        session,
        names.POLL_MAILBOX,
        queueing_lock=f"poll_mailbox:{mailbox_id}",
        lock=f"mailbox:{mailbox_id}",
        mailbox_id=str(mailbox_id),
    )


class MailboxService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._mailboxes = MailboxRepository(session)

    async def list_mailboxes(self) -> Sequence[Mailbox]:
        return await self._mailboxes.list_all()

    async def active_ids(self) -> Sequence[uuid.UUID]:
        return await self._mailboxes.active_ids()

    async def get(self, mailbox_id: uuid.UUID) -> Mailbox:
        mailbox = await self._mailboxes.get(mailbox_id)
        if mailbox is None:
            raise NotFoundError("Mailbox not found")
        return mailbox

    async def create(self, data: MailboxCreate) -> Mailbox:
        box = SecretBox(self._settings)
        mailbox = Mailbox(
            name=data.name,
            email_address=data.email_address,
            imap_host=data.imap_host,
            imap_port=data.imap_port,
            smtp_host=data.smtp_host,
            smtp_port=data.smtp_port,
            username=data.username,
            auth_type=MailboxAuthType.PASSWORD,
            encrypted_secret=box.encrypt(data.password.get_secret_value()),
            folder=data.folder,
        )
        self._mailboxes.add(mailbox)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("A mailbox with this address already exists") from exc
        logger.info("mailbox_created", mailbox_id=str(mailbox.id))
        return mailbox

    async def update(self, mailbox_id: uuid.UUID, data: MailboxUpdate) -> Mailbox:
        mailbox = await self.get(mailbox_id)
        for field in ("name", "imap_host", "imap_port", "smtp_host", "smtp_port", "username"):
            value = getattr(data, field)
            if value is not None:
                setattr(mailbox, field, value)
        if data.folder is not None and data.folder != mailbox.folder:
            # A different folder has its own UID sequence.
            mailbox.folder, mailbox.last_uid, mailbox.uidvalidity = data.folder, None, None
        if data.password is not None:
            mailbox.encrypted_secret = SecretBox(self._settings).encrypt(
                data.password.get_secret_value()
            )
            mailbox.last_error = None
        if data.is_active is not None:
            mailbox.is_active = data.is_active
        await self._session.commit()
        return mailbox

    async def request_poll(self, mailbox_id: uuid.UUID) -> bool:
        mailbox = await self.get(mailbox_id)
        queued = await enqueue_poll(self._session, mailbox.id)
        await self._session.commit()
        return queued

    async def poll(self, mailbox_id: uuid.UUID, reader: MailboxReader) -> dict[Outcome, int]:
        """Fetch new mail and ingest it. Progress (last UID) is saved per message, so a crash
        mid-batch resumes where it stopped; a message that can't be stored is recorded on the
        mailbox and skipped, so it can't block the inbox behind it."""
        mailbox = await self.get(mailbox_id)
        if not mailbox.is_active:
            return {}
        if mailbox.auth_type != MailboxAuthType.PASSWORD:
            await self._fail(mailbox_id, "OAuth2 (XOAUTH2) mailboxes are not supported yet")
            return {}
        try:
            password = SecretBox(self._settings).decrypt(mailbox.encrypted_secret)
        except AppError as exc:
            await self._fail(mailbox_id, exc.message)
            return {}

        ingestion_settings = await load_settings(self._session, IngestionSettings)
        since = (mailbox.last_polled_at or mailbox.created_at).date() - timedelta(
            days=ingestion_settings.initial_lookback_days
        )
        credentials = ImapCredentials(
            host=mailbox.imap_host,
            port=mailbox.imap_port,
            username=mailbox.username,
            password=password,
            folder=mailbox.folder,
        )
        last_uid, uidvalidity = mailbox.last_uid, mailbox.uidvalidity
        try:
            result = await anyio.to_thread.run_sync(
                lambda: reader.fetch_new(
                    credentials,
                    last_uid=last_uid,
                    uidvalidity=uidvalidity,
                    since=since,
                    limit=POLL_BATCH_SIZE,
                )
            )
        except Exception as exc:
            await self._fail(mailbox_id, f"IMAP error: {exc}")
            return {}

        if uidvalidity != result.uidvalidity:
            # Old UIDs are meaningless now. Commit before ingesting: a duplicate rolls the
            # session back, which would otherwise undo this.
            mailbox.uidvalidity, mailbox.last_uid = result.uidvalidity, None
            await self._session.commit()

        counts: dict[Outcome, int] = {}
        skipped: list[str] = []
        for email in result.emails:
            outcome = await self._ingest_one(mailbox_id, mailbox.email_address, email, skipped)
            if outcome is not None:
                counts[outcome] = counts.get(outcome, 0) + 1
            await self._advance(mailbox_id, email.uid)

        mailbox = await self._fresh(mailbox_id)
        mailbox.uidvalidity = result.uidvalidity
        mailbox.last_polled_at = datetime.now(UTC)
        mailbox.last_error = "; ".join(skipped)[:2000] or None
        await self._session.commit()
        logger.info("mailbox_polled", mailbox_id=str(mailbox_id), fetched=len(result.emails))
        return counts

    async def _ingest_one(
        self, mailbox_id: uuid.UUID, address: str, email: FetchedEmail, skipped: list[str]
    ) -> Outcome | None:
        try:
            parsed = parse_email(email.raw, mailbox_id=mailbox_id, mailbox_address=address)
            if parsed is None:
                return None
            return (await IngestionService(self._session).ingest(parsed)).outcome  # commits
        except Exception as exc:
            await self._session.rollback()
            logger.exception("mailbox_message_skipped", mailbox_id=str(mailbox_id), uid=email.uid)
            skipped.append(f"Skipped UID {email.uid}: {type(exc).__name__}")
            return None

    async def _advance(self, mailbox_id: uuid.UUID, uid: int) -> None:
        mailbox = await self._fresh(mailbox_id)
        mailbox.last_uid = max(mailbox.last_uid or 0, uid)
        await self._session.commit()

    async def _fresh(self, mailbox_id: uuid.UUID) -> Mailbox:
        """Re-read the row: the session may have been rolled back, and another process could
        have changed it (sessions keep objects across commits)."""
        mailbox = await self.get(mailbox_id)
        await self._session.refresh(mailbox)
        return mailbox

    async def _fail(self, mailbox_id: uuid.UUID, error: str) -> None:
        mailbox = await self._fresh(mailbox_id)
        mailbox.last_error = error[:2000]
        await self._session.commit()
        logger.warning("mailbox_poll_failed", mailbox_id=str(mailbox_id), error=error)
