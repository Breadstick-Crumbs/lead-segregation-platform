"""Working leads from the dashboard: list, read, change status or owner, reply, add notes.

Who may do what:
- Everyone signed in sees every lead (a small team works one shared pipeline) and may change
  a lead's status, reply and add notes.
- Admins assign any lead to anyone active. A sales rep may only claim an unassigned lead for
  themselves.

A human action is what stops the SLA clock: moving a lead out of `new` or replying records
`first_response_at` (docs/NOTIFICATIONS.md).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models import Contact, Lead, LeadEvent, LeadScore, Message, Notification, User
from app.models.enums import (
    Channel,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MessageDirection,
    NotificationKind,
    Role,
)
from app.repositories.lead_queries import LeadQueries, LeadRow
from app.repositories.leads import LeadRepository, MessageRepository
from app.repositories.notifications import NotificationRepository
from app.schemas.app_settings import SimilaritySettings
from app.schemas.lead import LeadFilters
from app.services.app_settings import load_settings
from app.services.auto_reply import WHATSAPP_WINDOW, AutoReplyService
from app.workers import names
from app.workers.queue import enqueue

SIMILAR_LIMIT = 5


def display_name(contact: Contact) -> str:
    return contact.company_name or contact.name or contact.email or contact.phone_e164 or "Unknown"


class LeadDetailData:
    """Everything the lead page shows, loaded in one service call."""

    def __init__(
        self,
        row: LeadRow,
        *,
        score: LeadScore | None,
        messages: list[Message],
        events: list[LeadEvent],
        duplicate: tuple[Lead, Contact] | None,
        reply_blocked_reason: str | None,
    ) -> None:
        self.row = row
        self.score = score
        self.messages = messages
        self.events = events
        self.duplicate = duplicate
        self.reply_blocked_reason = reply_blocked_reason


class SimilarLeadData:
    def __init__(self, lead: Lead, contact: Contact, similarity: float) -> None:
        self.lead = lead
        self.contact = contact
        self.similarity = similarity


class LeadService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._queries = LeadQueries(session)
        self._leads = LeadRepository(session)
        self._messages = MessageRepository(session)

    # Reading -------------------------------------------------------------------------

    async def page(self, filters: LeadFilters, *, viewer: User) -> tuple[list[LeadRow], int]:
        return await self._queries.page(filters, viewer_id=viewer.id)

    async def stats(self) -> dict[str, int | float | None]:
        now = datetime.now(UTC)
        counts = await self._queries.stats(
            since_24h=now - timedelta(hours=24), since_30d=now - timedelta(days=30)
        )
        closed = counts["closed_30d"]
        rate = round(counts["converted_30d"] / closed, 3) if closed else None
        return {**counts, "conversion_rate_30d": rate}

    async def detail(self, lead_id: uuid.UUID) -> LeadDetailData:
        row = await self._queries.row(lead_id)
        if row is None:
            raise NotFoundError("Lead not found")
        messages = list(await self._queries.messages(lead_id))
        duplicate = None
        if row.lead.possible_duplicate_of_id is not None:
            found = await self._queries.leads_with_contacts([row.lead.possible_duplicate_of_id])
            duplicate = found.get(row.lead.possible_duplicate_of_id)
        return LeadDetailData(
            row,
            score=await self._queries.latest_score(lead_id),
            messages=messages,
            events=list(await self._queries.events(lead_id)),
            duplicate=duplicate,
            reply_blocked_reason=_reply_blocked(row.lead, row.contact, messages),
        )

    async def similar(self, lead_id: uuid.UUID) -> list[SimilarLeadData]:
        lead = await self._leads.get(lead_id)
        if lead is None:
            raise NotFoundError("Lead not found")
        if lead.embedding is None:
            return []
        settings = await load_settings(self._session, SimilaritySettings)
        neighbours = await self._leads.similar(
            list(lead.embedding),
            exclude=lead.id,
            limit=SIMILAR_LIMIT,
            min_similarity=settings.similar_min,
        )
        found = await self._queries.leads_with_contacts([n.lead_id for n in neighbours])
        return [
            SimilarLeadData(*found[n.lead_id], n.similarity)
            for n in neighbours
            if n.lead_id in found
        ]

    # Changing ------------------------------------------------------------------------

    async def update(
        self,
        lead_id: uuid.UUID,
        *,
        actor: User,
        status: LeadStatus | None,
        owner_id: uuid.UUID | None,
        owner_given: bool,
    ) -> None:
        lead = await self._leads.get(lead_id, for_update=True)
        if lead is None:
            raise NotFoundError("Lead not found")
        now = datetime.now(UTC)
        if status is not None and status != lead.status:
            self._change_status(lead, status, actor=actor, at=now)
        if owner_given and owner_id != lead.owner_id:
            await self._change_owner(lead, owner_id, actor=actor)
        await self._session.commit()

    def _change_status(self, lead: Lead, status: LeadStatus, *, actor: User, at: datetime) -> None:
        previous = lead.status
        lead.status = status
        if previous == LeadStatus.NEW and lead.first_response_at is None:
            lead.first_response_at = at  # a person has handled it: the SLA is met
        if status == LeadStatus.CONVERTED:
            lead.converted_at = at
        elif previous == LeadStatus.CONVERTED:
            lead.converted_at = None
        self._session.add(
            LeadEvent(
                lead_id=lead.id,
                type=LeadEventType.STATUS_CHANGED,
                actor_id=actor.id,
                payload={"from": previous, "to": status},
            )
        )

    async def _change_owner(self, lead: Lead, owner_id: uuid.UUID | None, *, actor: User) -> None:
        claiming = owner_id == actor.id and lead.owner_id is None
        if actor.role != Role.ADMIN and not claiming:
            raise PermissionDeniedError(
                "Only admins can reassign leads; you can claim unassigned ones"
            )
        owner = None
        if owner_id is not None:
            owner = await self._session.get(User, owner_id)
            if owner is None or not owner.is_active:
                raise NotFoundError("That user doesn't exist or is inactive")
        lead.owner_id = owner_id
        self._session.add(
            LeadEvent(
                lead_id=lead.id,
                type=LeadEventType.ASSIGNED,
                actor_id=actor.id,
                payload={"owner_id": str(owner_id) if owner_id else None, "rule": "manual"},
            )
        )
        if owner is not None and owner.id != actor.id:
            contact = await self._session.get(Contact, lead.contact_id)
            who = display_name(contact) if contact else "a lead"
            await NotificationRepository(self._session).add(
                Notification(
                    user_id=owner.id,
                    lead_id=lead.id,
                    kind=NotificationKind.ASSIGNMENT,
                    title=f"{actor.full_name} assigned you {who}"[:200],
                    body=lead.summary or "",
                )
            )

    async def add_note(self, lead_id: uuid.UUID, body: str, *, actor: User) -> LeadEvent:
        lead = await self._leads.get(lead_id)
        if lead is None:
            raise NotFoundError("Lead not found")
        event = LeadEvent(
            lead_id=lead.id, type=LeadEventType.NOTE, actor_id=actor.id, payload={"text": body}
        )
        self._session.add(event)
        await self._session.commit()
        return event

    async def reply(self, lead_id: uuid.UUID, body: str, *, actor: User) -> Message:
        """Queue a reply on the lead's channel, threaded under the customer's latest message.
        Sending happens in the worker with the same at-most-once rules as auto-replies."""
        lead = await self._leads.get(lead_id, for_update=True)
        if lead is None:
            raise NotFoundError("Lead not found")
        contact = await self._session.get(Contact, lead.contact_id)
        messages = list(await self._messages.for_lead(lead.id))
        blocked = _reply_blocked(lead, contact, messages)
        if blocked is not None:
            raise ConflictError(blocked)
        trigger = [m for m in messages if m.direction == MessageDirection.INBOUND][-1]

        sender = AutoReplyService(self._session, self._settings, email=None, whatsapp=None)
        reply = await sender.build_outbound(lead, trigger, body, sent_by=actor.id)
        await self._messages.add(reply)
        now = datetime.now(UTC)
        if lead.status == LeadStatus.NEW:
            self._change_status(lead, LeadStatus.CONTACTED, actor=actor, at=now)
        elif lead.first_response_at is None:
            lead.first_response_at = now
        await enqueue(
            self._session,
            names.SEND_MESSAGE,
            queueing_lock=f"send_message:{reply.id}",
            lock=f"auto_reply:{lead.id}",  # never alongside this lead's auto-reply
            message_id=str(reply.id),
        )
        await self._session.commit()
        return reply


def _reply_blocked(lead: Lead, contact: Contact | None, messages: list[Message]) -> str | None:
    """Why a person can't reply right now, or None."""
    inbound = [m for m in messages if m.direction == MessageDirection.INBOUND]
    if not inbound or contact is None:
        return "There is no customer message to reply to"
    if lead.status == LeadStatus.SPAM or lead.category == LeadCategory.SPAM:
        return "This lead is spam: replying would confirm the address to the sender"
    if lead.source == Channel.WHATSAPP:
        return _whatsapp_blocked(contact, inbound[-1])
    if contact.email is None:
        return "The contact has no email address"
    if inbound[-1].mailbox_id is None:
        return "This lead didn't arrive through a mailbox we can send from"
    return None


def _whatsapp_blocked(contact: Contact, latest: Message) -> str | None:
    if contact.phone_e164 is None:
        return "The contact has no WhatsApp number"
    if datetime.now(UTC) - latest.occurred_at > WHATSAPP_WINDOW:
        return (
            "WhatsApp only allows free-form replies within 24 hours of the customer's last message"
        )
    return None
