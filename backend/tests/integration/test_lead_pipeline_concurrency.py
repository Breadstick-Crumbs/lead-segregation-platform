"""Races between the pipeline and ingestion, and between two pipeline runs. These need
separately committed transactions, so they use their own sessions and clean up after."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models import Contact, Lead, User
from app.models.enums import Channel, ProcessingState, Role
from app.repositories.leads import LeadRepository, SimilarLead
from app.schemas.inbound import InboundMessage
from app.services.ingestion import IngestionService
from app.services.lead_pipeline import LeadPipeline, Outcome
from tests.fakes.ai import FakeAnalyzer, FakeEmbedder

pytestmark = pytest.mark.integration

DOMAIN = "race-pipeline.test"


def _email(
    sender: str, body: str, *, message_id: str, refs: tuple[str, ...] = ()
) -> InboundMessage:
    return InboundMessage(
        channel=Channel.EMAIL,
        external_id=message_id,
        sender_address=sender,
        subject="Quote",
        body_text=body,
        thread_refs=refs,
        occurred_at=datetime.now(UTC),
    )


@pytest.fixture
async def sessions(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        contacts = select(Contact.id).where(Contact.email.like(f"%@{DOMAIN}"))
        await session.execute(delete(Lead).where(Lead.contact_id.in_(contacts)))
        await session.execute(delete(Contact).where(Contact.email.like(f"%@{DOMAIN}")))
        await session.execute(delete(User).where(User.email.like(f"%@{DOMAIN}")))
        await session.execute(text("DELETE FROM procrastinate_jobs"))
        await session.commit()


async def _ingest(factory: async_sessionmaker[AsyncSession], message: InboundMessage) -> uuid.UUID:
    async with factory() as session:
        result = await IngestionService(session).ingest(message)
    assert result.lead_id is not None
    return result.lead_id


async def test_a_follow_up_committed_while_the_result_is_saved_reopens_the_lead(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = f"buyer@{DOMAIN}"
    lead_id = await _ingest(sessions, _email(sender, "Quote for 5", message_id="<p1@race>"))
    follow_up: asyncio.Task[uuid.UUID] | None = None
    original = LeadRepository.similar

    async def similar_then_follow_up(
        self: LeadRepository, *args: object, **kwargs: object
    ) -> list[SimilarLead]:
        # Runs inside the write step, while the pipeline holds the lead's row lock: the
        # follow-up must wait for the save, then reopen the lead.
        nonlocal follow_up
        follow_up = asyncio.create_task(
            _ingest(
                sessions, _email(sender, "Make it 50", message_id="<p2@race>", refs=("<p1@race>",))
            )
        )
        await asyncio.sleep(0.5)
        return await original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(LeadRepository, "similar", similar_then_follow_up)
    async with sessions() as session:
        outcome = await LeadPipeline(session, FakeAnalyzer(), FakeEmbedder()).process(
            lead_id, final_attempt=False
        )
    assert follow_up is not None
    assert await follow_up == lead_id

    assert outcome == Outcome.PROCESSED
    async with sessions() as session:
        lead = await session.get(Lead, lead_id)
        assert lead is not None
        # Regression: the follow-up's state change was a no-op, so the lead stayed
        # `processed` and its new message was never scored.
        assert lead.processing_state == ProcessingState.PENDING

    monkeypatch.setattr(LeadRepository, "similar", original)
    analyzer = FakeAnalyzer()
    async with sessions() as session:
        again = await LeadPipeline(session, analyzer, FakeEmbedder()).process(
            lead_id, final_attempt=False
        )
    assert again == Outcome.PROCESSED
    assert len(analyzer.contexts[0].messages) == 2


async def test_leads_processed_at_once_go_to_different_reps(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        for i in range(2):
            session.add(User(email=f"rep{i}@{DOMAIN}", full_name=f"Rep {i}", role=Role.SALES_REP))
        await session.commit()
    leads = [
        await _ingest(sessions, _email(f"c{i}@{DOMAIN}", "Quote", message_id=f"<rr{i}@race>"))
        for i in range(2)
    ]

    async def run(lead_id: uuid.UUID) -> None:
        async with sessions() as session:
            await LeadPipeline(session, FakeAnalyzer(), FakeEmbedder()).process(
                lead_id, final_attempt=False
            )

    await asyncio.gather(*(run(lead_id) for lead_id in leads))

    async with sessions() as session:
        owners = (await session.scalars(select(Lead.owner_id).where(Lead.id.in_(leads)))).all()
    assert None not in owners
    assert len(set(owners)) == 2
