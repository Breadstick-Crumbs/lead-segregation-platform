import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Lead, LeadEvent, Message
from app.models.enums import (
    Channel,
    LeadEventType,
    LeadStatus,
    MessageKind,
    ProcessingState,
    TranscriptionStatus,
)
from app.schemas.inbound import InboundMessage
from app.services.ingestion import IngestionService, Outcome
from app.workers import names
from tests.integration.conftest import queued_jobs

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)

EMAIL = InboundMessage(
    channel=Channel.EMAIL,
    external_id="<q1@gulfbuild.ae>",
    sender_address="omar.haddad@gulfbuild.ae",
    sender_name="Omar Haddad",
    subject="Quotation for 40 seats",
    body_text="We need pricing for 40 seats this month.",
    occurred_at=NOW,
)
WHATSAPP = InboundMessage(
    channel=Channel.WHATSAPP,
    external_id="wamid.1",
    sender_address="+971501234567",
    sender_name="Fatima",
    body_text="Can I get a demo?",
    occurred_at=NOW,
)


async def _count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_first_message_creates_contact_lead_and_message(db_session: AsyncSession) -> None:
    result = await IngestionService(db_session).ingest(EMAIL)

    assert result.outcome == Outcome.NEW_LEAD
    lead = await db_session.get(Lead, result.lead_id)
    assert lead is not None
    assert (lead.source, lead.status, lead.processing_state) == (
        Channel.EMAIL,
        LeadStatus.NEW,
        ProcessingState.PENDING,
    )
    contact = await db_session.get(Contact, lead.contact_id)
    assert contact is not None
    assert (contact.email, contact.name, contact.company_domain) == (
        "omar.haddad@gulfbuild.ae",
        "Omar Haddad",
        "gulfbuild.ae",
    )
    events = (await db_session.scalars(select(LeadEvent.type).order_by(LeadEvent.created_at))).all()
    assert set(events) == {LeadEventType.CREATED, LeadEventType.MESSAGE_RECEIVED}
    assert await queued_jobs(db_session, names.PROCESS_LEAD) == [
        {"task": names.PROCESS_LEAD, "args": {"lead_id": str(lead.id)}}
    ]


async def test_the_same_message_twice_is_stored_once(db_session: AsyncSession) -> None:
    service = IngestionService(db_session)
    await service.ingest(EMAIL)

    again = await service.ingest(EMAIL)

    assert again.outcome == Outcome.DUPLICATE
    assert await _count(db_session, Message) == 1
    assert len(await queued_jobs(db_session)) == 1


async def test_an_email_reply_joins_its_thread(db_session: AsyncSession) -> None:
    service = IngestionService(db_session)
    first = await service.ingest(EMAIL)

    reply = replace(
        EMAIL,
        external_id="<q2@gulfbuild.ae>",
        body_text="Following up",
        thread_refs=("<q1@gulfbuild.ae>",),
    )
    second = await service.ingest(reply)

    assert second.outcome == Outcome.ATTACHED
    assert second.lead_id == first.lead_id
    # The lead's process job was already waiting, so the queueing lock kept just one.
    assert len(await queued_jobs(db_session, names.PROCESS_LEAD)) == 1


async def test_a_forged_reference_cannot_join_someone_elses_lead(db_session: AsyncSession) -> None:
    service = IngestionService(db_session)
    victim = await service.ingest(EMAIL)

    forged = replace(
        EMAIL,
        external_id="<evil@attacker.test>",
        sender_address="mallory@attacker.test",
        thread_refs=("<q1@gulfbuild.ae>",),
    )
    result = await service.ingest(forged)

    assert result.outcome == Outcome.NEW_LEAD
    assert result.lead_id != victim.lead_id


async def test_an_unthreaded_email_from_a_known_contact_is_a_new_lead(
    db_session: AsyncSession,
) -> None:
    service = IngestionService(db_session)
    await service.ingest(EMAIL)

    result = await service.ingest(replace(EMAIL, external_id="<other@x>", subject="New project"))

    assert result.outcome == Outcome.NEW_LEAD
    assert await _count(db_session, Contact) == 1


async def test_whatsapp_messages_in_the_window_continue_the_conversation(
    db_session: AsyncSession,
) -> None:
    service = IngestionService(db_session)
    first = await service.ingest(WHATSAPP)

    second = await service.ingest(replace(WHATSAPP, external_id="wamid.2", body_text="Tomorrow?"))

    assert second.outcome == Outcome.ATTACHED
    assert second.lead_id == first.lead_id


async def test_whatsapp_after_a_quiet_day_starts_a_new_lead(db_session: AsyncSession) -> None:
    service = IngestionService(db_session)
    old = await service.ingest(replace(WHATSAPP, occurred_at=NOW - timedelta(hours=30)))

    fresh = await service.ingest(replace(WHATSAPP, external_id="wamid.2"))

    assert fresh.outcome == Outcome.NEW_LEAD
    assert fresh.lead_id != old.lead_id


async def test_a_closed_lead_is_not_reopened(db_session: AsyncSession) -> None:
    service = IngestionService(db_session)
    first = await service.ingest(WHATSAPP)
    lead = await db_session.get(Lead, first.lead_id)
    assert lead is not None
    lead.status = LeadStatus.CONVERTED
    await db_session.commit()

    second = await service.ingest(replace(WHATSAPP, external_id="wamid.2"))

    assert second.outcome == Outcome.NEW_LEAD


async def test_a_follow_up_sends_a_processed_lead_back_to_the_pipeline(
    db_session: AsyncSession,
) -> None:
    service = IngestionService(db_session)
    first = await service.ingest(WHATSAPP)
    lead = await db_session.get(Lead, first.lead_id)
    assert lead is not None
    lead.processing_state = ProcessingState.PROCESSED
    await db_session.commit()

    await service.ingest(replace(WHATSAPP, external_id="wamid.2"))

    await db_session.refresh(lead)
    assert lead.processing_state == ProcessingState.PENDING


async def test_a_voice_note_waits_for_transcription(db_session: AsyncSession) -> None:
    voice = replace(
        WHATSAPP,
        external_id="wamid.v",
        body_text="",
        kind=MessageKind.VOICE,
        media_id="MEDIA1",
        media_mime_type="audio/ogg",
    )

    result = await IngestionService(db_session).ingest(voice)

    message = await db_session.get(Message, result.message_id)
    assert message is not None
    assert message.transcription_status == TranscriptionStatus.PENDING
    assert await queued_jobs(db_session) == [
        {"task": names.TRANSCRIBE_MESSAGE, "args": {"message_id": str(message.id)}}
    ]


async def test_a_later_message_fills_in_a_missing_contact_name(db_session: AsyncSession) -> None:
    service = IngestionService(db_session)
    await service.ingest(replace(WHATSAPP, sender_name=None))
    await service.ingest(replace(WHATSAPP, external_id="wamid.2", sender_name="Fatima"))
    await service.ingest(replace(WHATSAPP, external_id="wamid.3", sender_name="Someone else"))

    contact = await db_session.scalar(select(Contact))
    assert contact is not None
    assert contact.name == "Fatima"


async def test_free_webmail_senders_have_no_company_domain(db_session: AsyncSession) -> None:
    await IngestionService(db_session).ingest(
        replace(EMAIL, sender_address="someone@gmail.com", external_id=f"<{uuid.uuid4()}@x>")
    )

    contact = await db_session.scalar(select(Contact))
    assert contact is not None
    assert contact.company_domain is None
