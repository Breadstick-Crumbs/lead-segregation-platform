"""The automatic acknowledgement for leads scoring above the threshold (docs/SCORING.md).

One reply per lead, at most once:
- The outbound row is inserted first (`queued`); a unique index allows one auto-reply per
  lead, so concurrent or retried jobs can't create a second.
- `send_started_at` is committed just before the provider call. It is cleared only when the
  provider certainly didn't take the message, so a retry is safe. A row found `queued` with
  it still set means an earlier run died mid-send: the reply may have gone out, so it is
  marked failed ("outcome unknown") instead of being sent again.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import SecretBox, SecretsNotConfiguredError
from app.core.logging import get_logger
from app.integrations.smtp import (
    EmailSender,
    SendError,
    SmtpCredentials,
    compose_reply,
    new_message_id,
    reply_subject,
)
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.models import Contact, Lead, Mailbox, Message
from app.models.enums import (
    Channel,
    DeliveryStatus,
    LeadCategory,
    LeadEventType,
    MailboxAuthType,
    MessageDirection,
)
from app.repositories.leads import CLOSED_STATUSES, LeadRepository, MessageRepository
from app.schemas.app_settings import AutoReplyTemplates, ScoringThresholds
from app.services.app_settings import load_settings
from app.workers import names
from app.workers.queue import enqueue

logger = get_logger(__name__)

# Meta allows free-form messages only within 24 hours of the customer's last message.
WHATSAPP_WINDOW = timedelta(hours=24)
ERROR_CHARS = 1000


class AutoReplyOutcome(StrEnum):
    SENT = "sent"
    SKIPPED = "skipped"  # not eligible, or already handled
    FAILED = "failed"
    UNKNOWN = "unknown"  # an earlier run died mid-send; not resent


def wants_auto_reply(
    score: int | None, category: LeadCategory | None, thresholds: ScoringThresholds
) -> bool:
    return (
        score is not None
        and score > thresholds.auto_reply_above
        and category is not None
        and category != LeadCategory.SPAM
    )


async def enqueue_auto_reply(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    key = f"auto_reply:{lead_id}"
    # queueing_lock: one waiting job per lead; lock: never two running at once (a second job
    # can be queued while the first runs, e.g. after a quick follow-up).
    return await enqueue(
        session, names.SEND_AUTO_REPLY, queueing_lock=key, lock=key, lead_id=str(lead_id)
    )


def render_reply(templates: AutoReplyTemplates, *, language: str | None, name: str | None) -> str:
    """The template for the lead's language (else the default), with `{name}` filled in."""
    template = (
        templates.templates.get(language or "") or templates.templates[templates.default_language]
    )
    first_name = (name or "").split()[0] if (name or "").strip() else ""
    text = template.replace("{name}", first_name)
    # Without a name, "Hello {name}," must read "Hello," rather than "Hello ,".
    return re.sub(r" +([,.!?،])", r"\1", re.sub(r" {2,}", " ", text)).strip()


def _real_message_id(external_id: str | None) -> str | None:
    """Ids we made up for mail without a usable Message-ID can't be threaded on."""
    if not external_id or external_id.startswith(("<synthetic.", "<sha256.")):
        return None
    return external_id


def thread_references(stored_refs: str | None, parent: str | None) -> tuple[str, ...]:
    """`References` for our reply, oldest first (RFC 5322 §3.6.4): the customer's own chain,
    then their message. Ingestion stores a message's refs newest first (In-Reply-To, then
    References reversed), so they are reversed back and de-duplicated here."""
    chain: list[str] = []
    for ref in reversed((stored_refs or "").split()):
        if _real_message_id(ref) and ref not in chain and ref != parent:
            chain.append(ref)
    if parent:
        chain.append(parent)
    return tuple(chain)


class AutoReplyService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        email: EmailSender | None,
        whatsapp: WhatsAppClient | None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._email = email
        self._whatsapp = whatsapp
        self._leads = LeadRepository(session)
        self._messages = MessageRepository(session)

    async def send(self, lead_id: uuid.UUID, *, final_attempt: bool) -> AutoReplyOutcome:
        """The lead's automatic acknowledgement: created if it is due, then sent."""
        reply = await self._prepare(lead_id)
        if isinstance(reply, AutoReplyOutcome):
            return reply
        return await self._send_row(reply, final_attempt=final_attempt)

    async def send_message(self, message_id: uuid.UUID, *, final_attempt: bool) -> AutoReplyOutcome:
        """Send an outbound message that was already queued, e.g. a rep's reply from the
        dashboard. Same at-most-once rules as the auto-reply."""
        message = await self._session.get(Message, message_id)
        if message is None or message.direction != MessageDirection.OUTBOUND:
            return AutoReplyOutcome.SKIPPED
        resumed = await self._resume(message)
        if isinstance(resumed, AutoReplyOutcome):
            return resumed
        return await self._send_row(resumed, final_attempt=final_attempt)

    async def _send_row(self, reply: Message, *, final_attempt: bool) -> AutoReplyOutcome:
        lead_id = reply.lead_id
        reply_id = reply.id
        # Claim the send atomically: of two runs that both found the row unsent, only one
        # gets it back.
        if not await self._messages.claim_send(reply_id):
            await self._session.rollback()
            return AutoReplyOutcome.SKIPPED
        await self._session.commit()
        try:
            external_id = await self._deliver(reply)
        except (SendError, WhatsAppError) as exc:
            if exc.retryable and not final_attempt:
                reply.send_started_at = None  # certainly not sent: safe to try again
                await self._session.commit()
                logger.warning("outbound_retry", lead_id=str(lead_id), error=str(exc))
                raise
            return await self._finish_failed(reply, str(exc))
        except Exception as exc:
            # A bug mid-send: the message may or may not have left, so never resend it.
            logger.exception("outbound_crashed", lead_id=str(lead_id))
            await self._session.rollback()
            fresh = await self._session.get(Message, reply_id)
            if fresh is not None:
                await self._finish_failed(fresh, f"Outcome unknown: {type(exc).__name__}")
            raise

        # Logged before the commit: if the commit fails, the provider's id is still on record.
        logger.info("outbound_accepted", lead_id=str(lead_id), external_id=external_id)
        reply.external_id = external_id
        reply.delivery_status = DeliveryStatus.SENT
        reply.delivery_error = None
        reply.occurred_at = datetime.now(UTC)
        self._leads.add_event(
            reply.lead_id,
            _event_type(reply),
            {"message_id": str(reply.id), "channel": reply.channel, "status": "sent"},
        )
        await self._session.commit()
        logger.info("outbound_sent", lead_id=str(lead_id), channel=reply.channel)
        return AutoReplyOutcome.SENT

    async def _prepare(self, lead_id: uuid.UUID) -> Message | AutoReplyOutcome:
        """The reply row to send now, or why there is nothing to send."""
        existing = await self._messages.auto_reply_for(lead_id)
        if existing is not None:
            return await self._resume(existing)
        return await self._create(lead_id)

    async def _resume(self, existing: Message) -> Message | AutoReplyOutcome:
        if existing.delivery_status != DeliveryStatus.QUEUED:
            return AutoReplyOutcome.SKIPPED  # already sent or given up
        if existing.send_started_at is not None:
            await self._finish_failed(existing, "Outcome unknown: an earlier send was cut off")
            return AutoReplyOutcome.UNKNOWN
        return existing  # created, but the provider certainly hasn't seen it yet

    async def _create(self, lead_id: uuid.UUID) -> Message | AutoReplyOutcome:
        lead = await self._leads.get(lead_id)
        if lead is None:
            return AutoReplyOutcome.SKIPPED
        reason, trigger = await self._ineligible(lead)
        if reason is not None or trigger is None:
            await self._session.rollback()
            logger.info("auto_reply_skipped", lead_id=str(lead_id), reason=reason)
            return AutoReplyOutcome.SKIPPED

        reply = await self._new_reply(lead, trigger)
        try:
            await self._messages.add(reply)
            await self._session.commit()
        except IntegrityError:  # another run created it first
            await self._session.rollback()
            return AutoReplyOutcome.SKIPPED
        return reply

    async def _ineligible(self, lead: Lead) -> tuple[str | None, Message | None]:
        thresholds = await load_settings(self._session, ScoringThresholds)
        if lead.status in CLOSED_STATUSES:
            return "closed", None
        if not wants_auto_reply(lead.score, lead.category, thresholds):
            return "below_threshold_or_spam", None
        messages = await self._messages.for_lead(lead.id)
        if lead.first_response_at is not None or any(m.sent_by_user_id for m in messages):
            # Someone already answered (or moved it on): "we'll be in touch" would come late.
            return "already_answered", None
        inbound = [m for m in messages if m.direction == MessageDirection.INBOUND]
        if not inbound:
            return "no_inbound_message", None
        return _trigger_problem(lead, inbound[-1]), inbound[-1]

    async def _new_reply(self, lead: Lead, trigger: Message) -> Message:
        templates = await load_settings(self._session, AutoReplyTemplates)
        contact = await self._session.get(Contact, lead.contact_id)
        body = render_reply(
            templates, language=lead.language, name=contact.name if contact else None
        )
        return await self.build_outbound(lead, trigger, body, sent_by=None)

    async def build_outbound(
        self, lead: Lead, trigger: Message, body: str, *, sent_by: uuid.UUID | None
    ) -> Message:
        """A queued reply to `trigger` on its channel: threaded under it, from the mailbox it
        arrived at. `sent_by` None means the automatic acknowledgement."""
        templates = await load_settings(self._session, AutoReplyTemplates)
        if lead.source == Channel.EMAIL:
            mailbox = (
                await self._session.get(Mailbox, trigger.mailbox_id) if trigger.mailbox_id else None
            )
            sender = mailbox.email_address if mailbox else "unknown@invalid"
            return Message(
                lead_id=lead.id,
                mailbox_id=trigger.mailbox_id,
                channel=Channel.EMAIL,
                direction=MessageDirection.OUTBOUND,
                external_id=new_message_id(sender),  # fixed now, so replies thread back
                thread_ref=trigger.external_id,
                sender_address=sender,
                subject=reply_subject(trigger.subject, templates.subject_prefix)[:998],
                body_text=body,
                is_auto_reply=sent_by is None,
                sent_by_user_id=sent_by,
                delivery_status=DeliveryStatus.QUEUED,
                occurred_at=datetime.now(UTC),
            )
        return Message(
            lead_id=lead.id,
            channel=Channel.WHATSAPP,
            direction=MessageDirection.OUTBOUND,
            thread_ref=trigger.external_id,
            sender_address=self._settings.whatsapp_phone_number_id or "whatsapp",
            body_text=body,
            is_auto_reply=sent_by is None,
            sent_by_user_id=sent_by,
            delivery_status=DeliveryStatus.QUEUED,
            occurred_at=datetime.now(UTC),
        )

    async def _deliver(self, reply: Message) -> str | None:
        """Hand the reply to the provider; returns the provider's id for it."""
        lead = await self._leads.get(reply.lead_id)
        contact = await self._session.get(Contact, lead.contact_id) if lead else None
        if contact is None:
            raise SendError("The lead's contact is gone", retryable=False)
        if reply.channel == Channel.WHATSAPP:
            phone_number_id = self._settings.whatsapp_phone_number_id
            if self._whatsapp is None or not phone_number_id or contact.phone_e164 is None:
                raise SendError("WhatsApp sending is not configured", retryable=False)
            return await self._whatsapp.send_text(
                phone_number_id=phone_number_id,
                to=contact.phone_e164,
                body=reply.body_text,
                reply_to=reply.thread_ref,
            )

        mailbox = await self._session.get(Mailbox, reply.mailbox_id) if reply.mailbox_id else None
        if self._email is None or mailbox is None or not mailbox.is_active:
            raise SendError("The mailbox for this lead is not available", retryable=False)
        if mailbox.auth_type != MailboxAuthType.PASSWORD:
            raise SendError("OAuth2 (XOAUTH2) sending is not supported yet", retryable=False)
        if contact.email is None or reply.external_id is None:
            raise SendError("The contact has no email address", retryable=False)
        try:
            password = SecretBox(self._settings).decrypt(mailbox.encrypted_secret)
        except SecretsNotConfiguredError as exc:
            raise SendError(str(exc), retryable=False) from exc

        trigger = (
            await self._messages.by_external_id(Channel.EMAIL, reply.thread_ref)
            if reply.thread_ref
            else None
        )
        in_reply_to = _real_message_id(reply.thread_ref)
        references = thread_references(trigger.thread_ref if trigger else None, in_reply_to)
        try:
            message = compose_reply(
                from_name=mailbox.name,
                from_address=mailbox.email_address,
                to_address=contact.email,
                subject=reply.subject or "",
                body=reply.body_text,
                message_id=reply.external_id,
                in_reply_to=in_reply_to,
                references=references,
                automatic=reply.is_auto_reply,
            )
        except ValueError as exc:  # a header the email library refuses; nothing was sent
            raise SendError(f"Couldn't build the email: {exc}", retryable=False) from exc
        await self._email.send(
            message,
            SmtpCredentials(
                host=mailbox.smtp_host,
                port=mailbox.smtp_port,
                username=mailbox.username,
                password=password,
            ),
        )
        return reply.external_id

    async def _finish_failed(self, reply: Message, error: str) -> AutoReplyOutcome:
        reply.delivery_status = DeliveryStatus.FAILED
        reply.delivery_error = error[:ERROR_CHARS]
        self._leads.add_event(
            reply.lead_id,
            _event_type(reply),
            {"message_id": str(reply.id), "status": "failed", "error": error[:300]},
        )
        await self._session.commit()
        logger.error("outbound_failed", lead_id=str(reply.lead_id), error=error[:300])
        return AutoReplyOutcome.FAILED


def _event_type(message: Message) -> LeadEventType:
    return LeadEventType.AUTO_REPLIED if message.is_auto_reply else LeadEventType.REPLIED


def _trigger_problem(lead: Lead, trigger: Message) -> str | None:
    """Why the customer's latest message mustn't be acknowledged, or None."""
    if trigger.suppress_auto_reply:
        return "sender_opted_out"  # lists, robots, X-Auto-Response-Suppress
    if (
        lead.source == Channel.WHATSAPP
        and datetime.now(UTC) - trigger.occurred_at > WHATSAPP_WINDOW
    ):
        return "outside_whatsapp_window"
    return None
