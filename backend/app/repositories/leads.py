"""Data access for contacts, leads and messages."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Lead, LeadEvent, Message
from app.models.enums import Channel, LeadEventType, LeadStatus, MessageDirection

# Leads in these states are finished: a new message from the contact starts a fresh lead.
CLOSED_STATUSES = (LeadStatus.CONVERTED, LeadStatus.LOST, LeadStatus.SPAM)


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

    def add_event(self, lead_id: uuid.UUID, kind: LeadEventType, payload: dict[str, str]) -> None:
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
