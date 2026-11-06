"""Database-level guarantees that ingestion and auto-reply logic rely on."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Lead, Message
from app.models.enums import Channel, DeliveryStatus, MessageDirection
from tests.integration.conftest import CreateUser

pytestmark = pytest.mark.integration


async def _lead(db_session: AsyncSession) -> Lead:
    contact = Contact(email="buyer@acme.ae")
    db_session.add(contact)
    await db_session.flush()
    lead = Lead(contact_id=contact.id, source=Channel.EMAIL)
    db_session.add(lead)
    await db_session.flush()
    return lead


def _message(lead: Lead, **overrides: object) -> Message:
    values: dict[str, object] = {
        "lead_id": lead.id,
        "channel": Channel.EMAIL,
        "direction": MessageDirection.INBOUND,
        "external_id": "<abc@acme.ae>",
        "sender_address": "buyer@acme.ae",
        "body_text": "We would like a quote for 50 seats.",
        "delivery_status": DeliveryStatus.RECEIVED,
        "occurred_at": datetime.now(UTC),
    }
    return Message(**{**values, **overrides})


async def _assert_rejected(db_session: AsyncSession, row: object) -> None:
    async with db_session.begin_nested():
        db_session.add(row)
        with pytest.raises(IntegrityError):
            await db_session.flush()


async def test_the_same_inbound_message_cannot_be_stored_twice(db_session: AsyncSession) -> None:
    lead = await _lead(db_session)
    db_session.add(_message(lead))
    await db_session.flush()

    await _assert_rejected(db_session, _message(lead))


async def test_inbound_messages_need_an_external_id(db_session: AsyncSession) -> None:
    lead = await _lead(db_session)

    await _assert_rejected(db_session, _message(lead, external_id=None))


async def test_only_one_auto_reply_per_lead(db_session: AsyncSession) -> None:
    lead = await _lead(db_session)
    reply: dict[str, object] = {
        "direction": MessageDirection.OUTBOUND,
        "external_id": None,
        "is_auto_reply": True,
        "delivery_status": DeliveryStatus.QUEUED,
    }
    db_session.add(_message(lead, **reply))
    await db_session.flush()

    await _assert_rejected(db_session, _message(lead, **reply))


async def test_emails_must_be_lower_case(db_session: AsyncSession, create_user: CreateUser) -> None:
    await _assert_rejected(db_session, Contact(email="John@Acme.ae"))
    async with db_session.begin_nested():
        with pytest.raises(IntegrityError):
            await create_user("Mixed@Tawasol.test")


async def test_a_contact_needs_an_email_or_phone(db_session: AsyncSession) -> None:
    await _assert_rejected(db_session, Contact(name="Nobody"))
