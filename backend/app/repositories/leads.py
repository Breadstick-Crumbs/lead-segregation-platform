"""Data access for contacts, leads and messages."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Lead, LeadEvent, Message
from app.models.enums import (
    Channel,
    DeliveryStatus,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MessageDirection,
    ProcessingState,
    TranscriptionStatus,
)

# Leads in these states are finished: a new message from the contact starts a fresh lead.
CLOSED_STATUSES = (LeadStatus.CONVERTED, LeadStatus.LOST, LeadStatus.SPAM)


_DELIVERY_RANK = {
    DeliveryStatus.QUEUED: 1,
    DeliveryStatus.SENT: 2,
    DeliveryStatus.DELIVERED: 3,
    DeliveryStatus.READ: 4,
}


@dataclass(frozen=True)
class DeliveryUpdate:
    channel: Channel
    external_id: str
    status: DeliveryStatus
    error: str | None = None


@dataclass(frozen=True)
class SimilarLead:
    lead_id: uuid.UUID
    contact_id: uuid.UUID
    status: LeadStatus
    created_at: datetime
    similarity: float  # cosine similarity, 1 = identical


class ContactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, *, key: str, value: str, name: str | None, company_domain: str | None = None
    ) -> uuid.UUID:
        """Insert or find the contact by `key` ("email" or "phone_e164"); the first name we
        learn is kept, a missing one is filled in."""
        values: dict[str, object] = {key: value, "name": name}
        if key == "email":
            values["company_domain"] = company_domain
        statement = (
            insert(Contact)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[key],
                set_={"name": func.coalesce(Contact.name, insert(Contact).excluded.name)},
            )
            .returning(Contact.id)
        )
        contact_id: uuid.UUID = (await self._session.execute(statement)).scalar_one()
        return contact_id

    async def lock(self, contact_id: uuid.UUID) -> None:
        """Serialise ingestion per contact until the transaction ends, so concurrent messages
        from one person can't both decide to open a new lead."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"contact:{contact_id}"},
        )


class LeadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_thread(
        self, *, channel: Channel, refs: Sequence[str], contact_id: uuid.UUID
    ) -> Lead | None:
        """The open lead of this contact that contains one of the referenced messages."""
        lead: Lead | None = await self._session.scalar(
            select(Lead)
            .join(Message, Message.lead_id == Lead.id)
            .where(
                Message.channel == channel,
                Message.external_id.in_(refs),
                Lead.contact_id == contact_id,
                Lead.status.not_in(CLOSED_STATUSES),
            )
            .order_by(Message.occurred_at.desc())
            .limit(1)
        )
        return lead

    async def find_recent_open(
        self, *, channel: Channel, contact_id: uuid.UUID, since: datetime
    ) -> Lead | None:
        """The contact's open lead on this channel with an inbound message since `since`."""
        lead: Lead | None = await self._session.scalar(
            select(Lead)
            .join(Message, Message.lead_id == Lead.id)
            .where(
                Lead.contact_id == contact_id,
                Lead.source == channel,
                Lead.status.not_in(CLOSED_STATUSES),
                Message.direction == MessageDirection.INBOUND,
                Message.occurred_at >= since,
            )
            .order_by(Message.occurred_at.desc())
            .limit(1)
        )
        return lead

    async def add(self, lead: Lead) -> None:
        self._session.add(lead)
        await self._session.flush()

    async def mark_pending(self, lead_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Lead)
            .where(Lead.id == lead_id)
            .values(processing_state=ProcessingState.PENDING, updated_at=func.now())
            .execution_options(synchronize_session="fetch")
        )

    async def get(self, lead_id: uuid.UUID, *, for_update: bool = False) -> Lead | None:
        statement = select(Lead).where(Lead.id == lead_id)
        if for_update:
            statement = statement.with_for_update()
        lead: Lead | None = await self._session.scalar(statement)
        return lead

    async def has_pending_transcription(self, lead_id: uuid.UUID) -> bool:
        found = await self._session.scalar(
            select(
                exists().where(
                    Message.lead_id == lead_id,
                    Message.transcription_status == TranscriptionStatus.PENDING,
                )
            )
        )
        return bool(found)

    async def contact_history(self, lead: Lead) -> tuple[int, int]:
        """(earlier leads, converted leads) of this lead's contact. Spam doesn't count, nor do
        leads opened after this one (when an old lead is re-scored)."""
        row = (
            await self._session.execute(
                select(
                    func.count(),
                    func.count().filter(Lead.status == LeadStatus.CONVERTED),
                ).where(
                    Lead.contact_id == lead.contact_id,
                    Lead.id != lead.id,
                    Lead.created_at <= lead.created_at,
                    Lead.status != LeadStatus.SPAM,
                    or_(Lead.category.is_(None), Lead.category != LeadCategory.SPAM),
                )
            )
        ).one()
        return int(row[0]), int(row[1])

    async def similar(
        self, embedding: list[float], *, exclude: uuid.UUID, limit: int, min_similarity: float
    ) -> list[SimilarLead]:
        """Nearest leads by cosine distance (HNSW index), most similar first."""
        distance = Lead.embedding.cosine_distance(embedding)
        rows = await self._session.execute(
            select(Lead.id, Lead.contact_id, Lead.status, Lead.created_at, distance.label("d"))
            .where(Lead.embedding.is_not(None), Lead.id != exclude)
            .order_by(distance)
            .limit(limit)
        )
        return [
            SimilarLead(row.id, row.contact_id, row.status, row.created_at, 1 - float(row.d))
            for row in rows
            if 1 - float(row.d) >= min_similarity
        ]

    async def stale_pending_ids(self, *, before: datetime, limit: int) -> Sequence[uuid.UUID]:
        """Open leads waiting for the pipeline with no job queued or running for them (and
        none of their voice notes still being transcribed)."""
        transcribing = exists().where(
            Message.lead_id == Lead.id,
            Message.transcription_status == TranscriptionStatus.PENDING,
        )
        no_job = text(
            "NOT EXISTS (SELECT 1 FROM procrastinate_jobs j WHERE j.task_name = 'process_lead'"
            " AND j.status IN ('todo', 'doing') AND j.args->>'lead_id' = leads.id::text)"
        )
        result = await self._session.scalars(
            select(Lead.id)
            .where(
                Lead.processing_state == ProcessingState.PENDING,
                Lead.status.not_in(CLOSED_STATUSES),
                Lead.updated_at < before,
                ~transcribing,
                no_job,
            )
            .order_by(Lead.updated_at)
            .limit(limit)
        )
        return result.all()

    def add_event(self, lead_id: uuid.UUID, kind: LeadEventType, payload: dict[str, Any]) -> None:
        self._session.add(LeadEvent(lead_id=lead_id, type=kind, payload=payload))


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists(self, *, channel: Channel, external_id: str) -> bool:
        found = await self._session.scalar(
            select(Message.id).where(Message.channel == channel, Message.external_id == external_id)
        )
        return found is not None

    async def add(self, message: Message) -> None:
        self._session.add(message)
        await self._session.flush()

    async def by_external_id(self, channel: Channel, external_id: str) -> Message | None:
        message: Message | None = await self._session.scalar(
            select(Message).where(Message.channel == channel, Message.external_id == external_id)
        )
        return message

    async def claim_send(self, message_id: uuid.UUID) -> bool:
        """Mark a queued outbound message as being sent, unless another run already has."""
        claimed = await self._session.scalar(
            update(Message)
            .where(
                Message.id == message_id,
                Message.delivery_status == DeliveryStatus.QUEUED,
                Message.send_started_at.is_(None),
            )
            .values(send_started_at=func.now())
            .returning(Message.id)
            .execution_options(synchronize_session="fetch")
        )
        return claimed is not None

    async def auto_reply_for(self, lead_id: uuid.UUID) -> Message | None:
        message: Message | None = await self._session.scalar(
            select(Message).where(Message.lead_id == lead_id, Message.is_auto_reply)
        )
        return message

    async def apply_status(self, update_: DeliveryUpdate) -> bool:
        """Record a provider receipt on our outbound message. Statuses only move forward
        (sent → delivered → read); a late `failed` can't undo a delivery. Returns False when
        no message of ours has this id (yet)."""
        message: Message | None = await self._session.scalar(
            select(Message)
            .where(
                Message.channel == update_.channel,
                Message.external_id == update_.external_id,
                Message.direction == MessageDirection.OUTBOUND,
            )
            .with_for_update()
        )
        if message is None:
            return False
        current = _DELIVERY_RANK.get(message.delivery_status, 0)
        if update_.status == DeliveryStatus.FAILED:
            if current >= _DELIVERY_RANK[DeliveryStatus.DELIVERED]:
                return True  # a late `failed` can't undo a delivery
            message.delivery_error = update_.error
        elif _DELIVERY_RANK[update_.status] <= current:
            return True  # known message, stale receipt
        else:
            message.delivery_error = None
        message.delivery_status = update_.status
        return True

    async def for_lead(self, lead_id: uuid.UUID) -> Sequence[Message]:
        result = await self._session.scalars(
            select(Message).where(Message.lead_id == lead_id).order_by(Message.occurred_at)
        )
        return result.all()
