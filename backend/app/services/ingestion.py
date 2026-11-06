"""Turns a normalised inbound message into contact, lead and message rows, then enqueues the
next step, all in one transaction.

Guarantees:
- **Idempotent.** A message already stored (same channel and external id) is a no-op, so
  webhook retries and IMAP re-polls are safe.
- **One lead per conversation.** Ingestion is serialised per contact (row lock from the upsert
  plus an explicit advisory lock), so two messages arriving together can't both open a lead.
- **Nothing lost.** The follow-up job is enqueued in the same transaction as the rows.
- **Storable.** Every field is cleaned (no NUL) and fitted to its column, so a hostile header
  can't make the insert fail and block the queue behind it.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.text import clean, fit_identifier
from app.models import Lead, Message
from app.models.enums import (
    Channel,
    DeliveryStatus,
    LeadEventType,
    MessageDirection,
    MessageKind,
    ProcessingState,
    TranscriptionStatus,
)
from app.repositories.leads import ContactRepository, LeadRepository, MessageRepository
from app.schemas.app_settings import IngestionSettings
from app.schemas.inbound import InboundMessage
from app.services.app_settings import load_settings
from app.workers import names
from app.workers.queue import enqueue

logger = get_logger(__name__)

EXTERNAL_ID_MAX = 512
FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "yandex.com",
        "mail.com",
    }
)


class Outcome(StrEnum):
    NEW_LEAD = "new_lead"
    ATTACHED = "attached"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class IngestionResult:
    outcome: Outcome
    lead_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None


def company_domain(email: str) -> str | None:
    domain = email.rpartition("@")[2].lower()
    return None if not domain or domain in FREE_EMAIL_DOMAINS else domain[:253]


class IngestionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._contacts = ContactRepository(session)
        self._leads = LeadRepository(session)
        self._messages = MessageRepository(session)

    async def ingest(self, message: InboundMessage) -> IngestionResult:
        """Store one message and enqueue its processing. Commits."""
        external_id = fit_identifier(message.external_id, EXTERNAL_ID_MAX)
        thread_refs = tuple(fit_identifier(ref, EXTERNAL_ID_MAX) for ref in message.thread_refs)

        is_email = message.channel == Channel.EMAIL
        contact_id = await self._contacts.upsert(
            key="email" if is_email else "phone_e164",
            value=message.sender_address,
            name=clean(message.sender_name, 200) or None,
            company_domain=company_domain(message.sender_address) if is_email else None,
        )
        await self._contacts.lock(contact_id)
        if await self._messages.exists(channel=message.channel, external_id=external_id):
            await self._session.rollback()  # releases the lock; nothing else was changed
            logger.info("inbound_duplicate", channel=message.channel, external_id=external_id)
            return IngestionResult(Outcome.DUPLICATE)

        lead = await self._conversation_lead(message.channel, thread_refs, contact_id)
        outcome = Outcome.ATTACHED if lead is not None else Outcome.NEW_LEAD
        if lead is None:
            lead = Lead(contact_id=contact_id, source=message.channel)
            await self._leads.add(lead)
            self._leads.add_event(lead.id, LeadEventType.CREATED, {"channel": message.channel})
        else:
            # New information: the AI pipeline re-reads the conversation.
            lead.processing_state = ProcessingState.PENDING

        stored = self._message_row(message, lead.id, external_id, thread_refs)
        await self._messages.add(stored)
        self._leads.add_event(
            lead.id,
            LeadEventType.MESSAGE_RECEIVED,
            {"message_id": str(stored.id), "kind": message.kind},
        )

        if stored.transcription_status == TranscriptionStatus.PENDING:
            await enqueue(self._session, names.TRANSCRIBE_MESSAGE, message_id=str(stored.id))
        else:
            await enqueue(
                self._session,
                names.PROCESS_LEAD,
                queueing_lock=f"process_lead:{lead.id}",
                lead_id=str(lead.id),
            )
        await self._session.commit()
        logger.info(
            "inbound_stored",
            outcome=outcome,
            channel=message.channel,
            lead_id=str(lead.id),
            message_id=str(stored.id),
        )
        return IngestionResult(outcome, lead.id, stored.id)

    async def _conversation_lead(
        self, channel: Channel, thread_refs: tuple[str, ...], contact_id: uuid.UUID
    ) -> Lead | None:
        if thread_refs:
            # A reply to one of our stored messages continues that lead, but only for the same
            # contact: a forged In-Reply-To must not inject mail into someone else's lead.
            lead = await self._leads.find_by_thread(
                channel=channel, refs=thread_refs, contact_id=contact_id
            )
            if lead is not None or channel == Channel.EMAIL:
                return lead
        if channel == Channel.WHATSAPP:
            # WhatsApp has no threads: an open lead with a recent message is the conversation.
            settings = await load_settings(self._session, IngestionSettings)
            since = datetime.now(UTC) - timedelta(hours=settings.whatsapp_conversation_hours)
            return await self._leads.find_recent_open(
                channel=channel, contact_id=contact_id, since=since
            )
        return None

    @staticmethod
    def _message_row(
        message: InboundMessage,
        lead_id: uuid.UUID,
        external_id: str,
        thread_refs: tuple[str, ...],
    ) -> Message:
        needs_transcript = message.kind == MessageKind.VOICE and message.media_id is not None
        return Message(
            lead_id=lead_id,
            mailbox_id=message.mailbox_id,
            channel=message.channel,
            direction=MessageDirection.INBOUND,
            external_id=external_id,
            thread_ref=" ".join(thread_refs) or None,
            sender_address=message.sender_address,
            subject=clean(message.subject, 998) or None,
            body_text=clean(message.body_text) or "",
            kind=message.kind,
            media_id=clean(message.media_id, 200),
            media_mime_type=clean(message.media_mime_type, 100),
            transcription_status=TranscriptionStatus.PENDING if needs_transcript else None,
            suppress_auto_reply=message.suppress_auto_reply,
            delivery_status=DeliveryStatus.RECEIVED,
            occurred_at=message.occurred_at,
        )
