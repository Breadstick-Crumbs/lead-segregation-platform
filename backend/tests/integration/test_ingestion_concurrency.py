"""Races need separately committed transactions, so this test uses its own sessions.

Concurrent ingestion for one contact is serialised twice over: the contact upsert's row
lock and an explicit advisory lock (kept so correctness doesn't depend on the upsert
shape). This test checks the outcome, not which lock provides it."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.models import Contact, Lead, Message
from app.models.enums import Channel
from app.schemas.inbound import InboundMessage
from app.services.ingestion import IngestionService

pytestmark = pytest.mark.integration

PHONE = "+971509999999"
FIRST = InboundMessage(
    channel=Channel.WHATSAPP,
    external_id="wamid.race.0",
    sender_address=PHONE,
    body_text="Hello",
    occurred_at=datetime.now(UTC),
)


async def test_simultaneous_first_messages_from_one_person_open_one_lead(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _ingest(i: int) -> None:
        async with sessions() as session:
            await IngestionService(session).ingest(replace(FIRST, external_id=f"wamid.race.{i}"))

    try:
        await asyncio.gather(*(_ingest(i) for i in range(6)))

        async with sessions() as session:
            contact = await session.scalar(select(Contact).where(Contact.phone_e164 == PHONE))
            assert contact is not None
            leads = await session.scalar(
                select(func.count()).select_from(Lead).where(Lead.contact_id == contact.id)
            )
            messages = await session.scalar(
                select(func.count())
                .select_from(Message)
                .join(Lead)
                .where(Lead.contact_id == contact.id)
            )
        assert (leads, messages) == (1, 6)
    finally:
        async with sessions() as session:
            contact_ids = select(Contact.id).where(Contact.phone_e164 == PHONE)
            await session.execute(delete(Lead).where(Lead.contact_id.in_(contact_ids)))
            await session.execute(delete(Contact).where(Contact.phone_e164 == PHONE))
            await session.execute(text("DELETE FROM procrastinate_jobs"))
            await session.commit()
