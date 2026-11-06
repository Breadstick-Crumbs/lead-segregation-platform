"""The AI pipeline against a real database, with fake OpenAI classes.

The pipeline ends its read transaction before calling OpenAI, so every test commits its
setup (here, commits release a savepoint; the outer test transaction still rolls back)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.openai_llm import PROMPT_VERSION, AIError
from app.models import Contact, Lead, LeadEvent, LeadScore, Message, User
from app.models.enums import (
    Channel,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MessageKind,
    Priority,
    ProcessingState,
    Role,
    TranscriptionStatus,
)
from app.schemas.analysis import LeadContext
from app.schemas.inbound import InboundMessage
from app.services.ingestion import IngestionService
from app.services.lead_pipeline import LeadPipeline, Outcome
from app.services.lead_queue import sweep_pending_leads
from app.workers import names
from tests.fakes.ai import FakeAnalyzer, FakeEmbedder, analysis, unit_vector
from tests.integration.conftest import CreateUser, queued_jobs

pytestmark = pytest.mark.integration


def _email(
    body: str = "Please send a quote for 40 seats, we need it this week.",
    *,
    sender: str = "omar@gulfbuild.ae",
    message_id: str | None = None,
    refs: tuple[str, ...] = (),
) -> InboundMessage:
    return InboundMessage(
        channel=Channel.EMAIL,
        external_id=message_id or f"<{uuid.uuid4()}@gulfbuild.ae>",
        sender_address=sender,
        sender_name="Omar Haddad",
        subject="Quote request",
        body_text=body,
        thread_refs=refs,
        occurred_at=datetime.now(UTC),
    )


async def _ingest(session: AsyncSession, message: InboundMessage) -> uuid.UUID:
    result = await IngestionService(session).ingest(message)
    assert result.lead_id is not None
    return result.lead_id


async def _run(
    session: AsyncSession,
    lead_id: uuid.UUID,
    analyzer: FakeAnalyzer | None = None,
    embedder: FakeEmbedder | None = None,
    *,
    final_attempt: bool = False,
) -> Outcome:
    pipeline = LeadPipeline(session, analyzer or FakeAnalyzer(), embedder or FakeEmbedder())
    return await pipeline.process(lead_id, final_attempt=final_attempt)


async def _lead(session: AsyncSession, lead_id: uuid.UUID) -> Lead:
    session.expire_all()
    lead = await session.get(Lead, lead_id)
    assert lead is not None
    return lead


async def _events(session: AsyncSession, lead_id: uuid.UUID) -> list[LeadEventType]:
    rows = await session.scalars(
        select(LeadEvent.type).where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.created_at)
    )
    return list(rows)


async def _reps(create_user: CreateUser, session: AsyncSession, count: int) -> list[uuid.UUID]:
    ids = [(await create_user(f"rep{i}@tawasol.test")).id for i in range(count)]
    await session.commit()
    return ids


async def test_a_lead_is_classified_scored_and_assigned(
    db_session: AsyncSession, create_user: CreateUser
) -> None:
    [rep] = await _reps(create_user, db_session, 1)
    lead_id = await _ingest(db_session, _email())
    analyzer = FakeAnalyzer()

    assert await _run(db_session, lead_id, analyzer) == Outcome.PROCESSED

    lead = await _lead(db_session, lead_id)
    assert lead.category == LeadCategory.SALES_OPPORTUNITY
    assert lead.processing_state == ProcessingState.PROCESSED
    assert lead.score is not None
    assert lead.priority == Priority.HIGH
    assert lead.summary == "Wants a quote for 40 seats"
    assert lead.language == "en"
    assert lead.owner_id == rep
    assert lead.embedding is not None
    assert len(lead.embedding) == 1536

    score = await db_session.scalar(select(LeadScore).where(LeadScore.lead_id == lead_id))
    assert score is not None
    assert score.total == lead.score
    assert score.prompt_version == PROMPT_VERSION
    assert score.model == "fake-model"
    assert score.breakdown["keywords"]["matched"] == ["quote"]
    assert score.breakdown["company"]["signals"] == [
        "corporate_domain",
        "company_name",
        "job_title",
    ]

    contact = await db_session.get(Contact, lead.contact_id)
    assert contact is not None
    assert contact.company_name == "Gulf Build LLC"
    assert await _events(db_session, lead_id) == [
        LeadEventType.CREATED,
        LeadEventType.MESSAGE_RECEIVED,
        LeadEventType.CLASSIFIED,
        LeadEventType.SCORED,
        LeadEventType.ASSIGNED,
    ]
    [context] = analyzer.contexts
    assert context.company_domain == "gulfbuild.ae"
    assert context.messages[0].text.startswith("Please send a quote")


async def test_assignment_is_round_robin_over_active_reps(
    db_session: AsyncSession, create_user: CreateUser
) -> None:
    first, second = await _reps(create_user, db_session, 2)
    await create_user("off@tawasol.test", is_active=False)
    await create_user("boss@tawasol.test", role=Role.ADMIN)
    await db_session.commit()

    owners = []
    for i in range(3):
        lead_id = await _ingest(db_session, _email(sender=f"buyer{i}@example.org"))
        await _run(db_session, lead_id)
        owners.append((await _lead(db_session, lead_id)).owner_id)

    # Reps created together tie on created_at, so either may go first.
    assert set(owners[:2]) == {first, second}
    assert owners[2] == owners[0]


async def test_spam_is_not_assigned_and_stays_low(
    db_session: AsyncSession, create_user: CreateUser
) -> None:
    await _reps(create_user, db_session, 1)
    lead_id = await _ingest(db_session, _email("Cheap SEO backlinks, urgent pricing offer!!!"))

    await _run(db_session, lead_id, FakeAnalyzer(analysis(category=LeadCategory.SPAM, intent=0)))

    lead = await _lead(db_session, lead_id)
    assert lead.score == 5
    assert lead.priority == Priority.LOW
    assert lead.owner_id is None


async def test_without_reps_the_lead_is_left_unassigned(db_session: AsyncSession) -> None:
    lead_id = await _ingest(db_session, _email())

    assert await _run(db_session, lead_id) == Outcome.PROCESSED
    assert (await _lead(db_session, lead_id)).owner_id is None


async def test_a_follow_up_during_the_call_supersedes_the_result(db_session: AsyncSession) -> None:
    first = _email(message_id="<first@gulfbuild.ae>")
    lead_id = await _ingest(db_session, first)
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()

    async def follow_up(_: LeadContext) -> None:
        await _ingest(db_session, _email("Also 10 more seats", refs=("<first@gulfbuild.ae>",)))

    outcome = await _run(db_session, lead_id, FakeAnalyzer(before=follow_up))

    assert outcome == Outcome.SUPERSEDED
    lead = await _lead(db_session, lead_id)
    assert lead.processing_state == ProcessingState.PENDING
    assert lead.score is None
    assert await db_session.scalar(select(LeadScore).where(LeadScore.lead_id == lead_id)) is None
    # The follow-up queued its own run, which will see both messages.
    assert len(await queued_jobs(db_session, names.PROCESS_LEAD)) == 1


async def test_a_follow_up_rescores_and_keeps_the_owner(
    db_session: AsyncSession, create_user: CreateUser
) -> None:
    await _reps(create_user, db_session, 2)
    lead_id = await _ingest(db_session, _email(message_id="<a@gulfbuild.ae>"))
    await _run(db_session, lead_id, FakeAnalyzer(analysis(intent=40)))
    processed = await _lead(db_session, lead_id)
    before, owner = processed.score, processed.owner_id

    await _ingest(db_session, _email("Signing today", refs=("<a@gulfbuild.ae>",)))
    analyzer = FakeAnalyzer(analysis(intent=100, urgency=100))
    assert await _run(db_session, lead_id, analyzer) == Outcome.PROCESSED

    lead = await _lead(db_session, lead_id)
    assert owner is not None
    assert lead.owner_id == owner
    assert lead.score is not None
    assert before is not None
    assert lead.score > before
    assert len(analyzer.contexts[0].messages) == 2
    scores = await db_session.scalars(select(LeadScore.total).where(LeadScore.lead_id == lead_id))
    assert len(list(scores)) == 2


async def test_leads_waiting_for_a_transcript_are_skipped(db_session: AsyncSession) -> None:
    voice = InboundMessage(
        channel=Channel.WHATSAPP,
        external_id="wamid.voice.1",
        sender_address="+971501112222",
        body_text="",
        kind=MessageKind.VOICE,
        media_id="media-1",
        media_mime_type="audio/ogg",
        occurred_at=datetime.now(UTC),
    )
    lead_id = await _ingest(db_session, voice)
    analyzer = FakeAnalyzer()

    assert await _run(db_session, lead_id, analyzer) == Outcome.SKIPPED
    assert analyzer.contexts == []


@pytest.mark.parametrize("status", [LeadStatus.CONVERTED, LeadStatus.SPAM])
async def test_closed_leads_are_skipped(db_session: AsyncSession, status: LeadStatus) -> None:
    lead_id = await _ingest(db_session, _email())
    await db_session.execute(update(Lead).where(Lead.id == lead_id).values(status=status))
    await db_session.commit()

    assert await _run(db_session, lead_id) == Outcome.SKIPPED


async def test_an_already_processed_lead_is_not_processed_twice(db_session: AsyncSession) -> None:
    lead_id = await _ingest(db_session, _email())
    await _run(db_session, lead_id)
    analyzer = FakeAnalyzer()

    assert await _run(db_session, lead_id, analyzer) == Outcome.SKIPPED
    assert analyzer.contexts == []


async def test_a_retryable_error_retries_then_fails_cleanly(db_session: AsyncSession) -> None:
    lead_id = await _ingest(db_session, _email())
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))  # as if this job is running
    await db_session.commit()
    analyzer = FakeAnalyzer(error=AIError("rate limited", retryable=True))

    with pytest.raises(AIError):
        await _run(db_session, lead_id, analyzer)
    assert (await _lead(db_session, lead_id)).processing_state == ProcessingState.PENDING

    assert await _run(db_session, lead_id, analyzer, final_attempt=True) == Outcome.FAILED
    lead = await _lead(db_session, lead_id)
    assert lead.processing_state == ProcessingState.FAILED
    assert lead.processing_error == "rate limited"
    assert LeadEventType.PROCESSING_FAILED in await _events(db_session, lead_id)


async def test_an_unexpected_error_on_the_last_attempt_fails_the_lead(
    db_session: AsyncSession,
) -> None:
    lead_id = await _ingest(db_session, _email())
    analyzer = FakeAnalyzer(error=KeyError("boom"))

    assert await _run(db_session, lead_id, analyzer, final_attempt=True) == Outcome.FAILED
    assert (await _lead(db_session, lead_id)).processing_error == "KeyError: 'boom'"


async def test_without_openai_configured_the_lead_fails_at_once(db_session: AsyncSession) -> None:
    lead_id = await _ingest(db_session, _email())

    outcome = await LeadPipeline(db_session, None, None).process(lead_id, final_attempt=False)

    assert outcome == Outcome.FAILED
    assert (await _lead(db_session, lead_id)).processing_error == "OpenAI is not configured"


async def test_a_failure_does_not_hide_a_follow_up(db_session: AsyncSession) -> None:
    lead_id = await _ingest(db_session, _email(message_id="<f@gulfbuild.ae>"))

    async def follow_up(_: LeadContext) -> None:
        await _ingest(db_session, _email("hello again", refs=("<f@gulfbuild.ae>",)))

    analyzer = FakeAnalyzer(error=AIError("down", retryable=True), before=follow_up)
    outcome = await _run(db_session, lead_id, analyzer, final_attempt=True)

    assert outcome == Outcome.SUPERSEDED
    assert (await _lead(db_session, lead_id)).processing_state == ProcessingState.PENDING


async def test_history_counts_the_contacts_earlier_conversion(db_session: AsyncSession) -> None:
    earlier = await _ingest(db_session, _email())
    await _run(db_session, earlier)
    await db_session.execute(
        update(Lead).where(Lead.id == earlier).values(status=LeadStatus.CONVERTED)
    )
    await db_session.commit()

    later = await _ingest(db_session, _email("Ready to order again"))
    await _run(db_session, later)

    score = await db_session.scalar(select(LeadScore).where(LeadScore.lead_id == later))
    assert score is not None
    history = score.breakdown["history"]
    assert (history["prior_leads"], history["prior_conversions"]) == (1, 1)
    assert history["value"] == 55  # 15 for the earlier lead + 40 for its conversion


async def test_similar_leads_that_converted_raise_the_history_factor(
    db_session: AsyncSession,
) -> None:
    embedder = FakeEmbedder({"fit-out": unit_vector(7, tilt=0.4)}, default=3)
    won = await _ingest(db_session, _email("Office fit-out for 3 floors", sender="a@one.ae"))
    lost = await _ingest(db_session, _email("Office fit-out, 2 floors", sender="b@two.ae"))
    for lead_id in (won, lost):
        await _run(db_session, lead_id, embedder=embedder)
    await db_session.execute(update(Lead).where(Lead.id == won).values(status=LeadStatus.CONVERTED))
    await db_session.execute(update(Lead).where(Lead.id == lost).values(status=LeadStatus.LOST))
    await db_session.commit()

    new = await _ingest(db_session, _email("Fit-out quote", sender="c@three.ae"))
    embedder.by_marker = {"Fit-out": unit_vector(7, tilt=0.5)}
    await _run(db_session, new, embedder=embedder)

    score = await db_session.scalar(select(LeadScore).where(LeadScore.lead_id == new))
    assert score is not None
    assert score.breakdown["history"]["similar_decided"] == 2
    assert score.breakdown["history"]["similar_conversion_rate"] == 0.5
    assert score.breakdown["history"]["value"] == 15


async def test_near_identical_text_from_another_address_is_flagged(
    db_session: AsyncSession,
) -> None:
    embedder = FakeEmbedder({"warehouse": unit_vector(11)})
    original = await _ingest(db_session, _email("Quote for warehouse racking", sender="x@a.ae"))
    await _run(db_session, original, embedder=embedder)

    copy = await _ingest(db_session, _email("Quote for warehouse racking!", sender="x@gmail.com"))
    embedder.by_marker = {"warehouse": unit_vector(11, tilt=0.05)}
    await _run(db_session, copy, embedder=embedder)

    assert (await _lead(db_session, copy)).possible_duplicate_of_id == original
    assert (await _lead(db_session, original)).possible_duplicate_of_id is None


async def test_old_or_merely_similar_leads_are_not_duplicates(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder({"racking": unit_vector(12)})
    old = await _ingest(db_session, _email("Pallet racking quote", sender="y@a.ae"))
    await _run(db_session, old, embedder=embedder)
    await db_session.execute(
        update(Lead).where(Lead.id == old).values(created_at=datetime.now(UTC) - timedelta(days=30))
    )
    await db_session.commit()

    similar = await _ingest(db_session, _email("Pallet racking quote", sender="z@b.ae"))
    await _run(db_session, similar, embedder=embedder)

    assert (await _lead(db_session, similar)).possible_duplicate_of_id is None


async def test_the_sweep_queues_stranded_pending_leads_once(db_session: AsyncSession) -> None:
    stranded = await _ingest(db_session, _email())
    recent = await _ingest(db_session, _email(sender="new@example.org"))
    voice = await _ingest(
        db_session,
        InboundMessage(
            channel=Channel.WHATSAPP,
            external_id="wamid.voice.2",
            sender_address="+971503334444",
            body_text="",
            kind=MessageKind.VOICE,
            media_id="m",
            occurred_at=datetime.now(UTC),
        ),
    )
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    await db_session.execute(
        update(Lead).where(Lead.id.in_([stranded, voice])).values(updated_at=long_ago)
    )
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()

    assert await sweep_pending_leads(db_session) == 1
    assert await sweep_pending_leads(db_session) == 0  # its job is already waiting

    jobs = await queued_jobs(db_session, names.PROCESS_LEAD)
    assert [job["args"]["lead_id"] for job in jobs] == [str(stranded)]
    assert recent != stranded
    transcribing = await db_session.scalar(
        select(Message.transcription_status).where(Message.lead_id == voice)
    )
    assert transcribing == TranscriptionStatus.PENDING


async def test_the_contact_keeps_a_company_name_it_already_has(db_session: AsyncSession) -> None:
    lead_id = await _ingest(db_session, _email())
    contact_id = (await _lead(db_session, lead_id)).contact_id
    await db_session.execute(
        update(Contact).where(Contact.id == contact_id).values(company_name="Known Co")
    )
    await db_session.commit()

    await _run(db_session, lead_id)

    contact = await db_session.get(Contact, contact_id)
    assert contact is not None
    await db_session.refresh(contact)
    assert contact.company_name == "Known Co"


async def test_last_assigned_at_moves_with_each_assignment(
    db_session: AsyncSession, create_user: CreateUser
) -> None:
    [rep] = await _reps(create_user, db_session, 1)
    lead_id = await _ingest(db_session, _email())

    await _run(db_session, lead_id)

    db_session.expire_all()
    user = await db_session.get(User, rep)
    assert user is not None
    assert user.last_assigned_at is not None


async def test_a_retry_steps_aside_when_a_newer_job_is_queued(db_session: AsyncSession) -> None:
    # Regression: retrying while a follow-up's job waited clashed with its queueing lock and
    # left the running job stuck. The waiting job covers the lead, so this one just ends.
    lead_id = await _ingest(db_session, _email())  # its job is still `todo`
    analyzer = FakeAnalyzer(error=AIError("rate limited", retryable=True))

    assert await _run(db_session, lead_id, analyzer) == Outcome.SUPERSEDED
    assert (await _lead(db_session, lead_id)).processing_state == ProcessingState.PENDING


async def test_a_failing_write_step_fails_the_lead_on_the_last_attempt(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    lead_id = await _ingest(db_session, _email())

    async def broken(*_: object, **__: object) -> None:
        raise RuntimeError("deadlock detected")

    monkeypatch.setattr(LeadPipeline, "_apply", broken)

    assert await _run(db_session, lead_id, final_attempt=True) == Outcome.FAILED
    lead = await _lead(db_session, lead_id)
    assert lead.processing_error == "RuntimeError: deadlock detected"
    assert await db_session.scalar(select(LeadScore).where(LeadScore.lead_id == lead_id)) is None


async def test_the_sweep_skips_leads_whose_job_is_running(db_session: AsyncSession) -> None:
    lead_id = await _ingest(db_session, _email())
    await db_session.execute(
        update(Lead)
        .where(Lead.id == lead_id)
        .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
    )
    await db_session.execute(text("UPDATE procrastinate_jobs SET status = 'doing'"))
    await db_session.commit()

    assert await sweep_pending_leads(db_session) == 0


async def test_spam_leads_do_not_count_as_history(db_session: AsyncSession) -> None:
    spam = await _ingest(db_session, _email("Buy followers"))
    await _run(db_session, spam, FakeAnalyzer(analysis(category=LeadCategory.SPAM)))

    real = await _ingest(db_session, _email("Quote please"))
    await _run(db_session, real)

    score = await db_session.scalar(select(LeadScore).where(LeadScore.lead_id == real))
    assert score is not None
    assert score.breakdown["history"]["prior_leads"] == 0


async def test_identical_spam_from_many_senders_is_not_flagged_duplicate(
    db_session: AsyncSession,
) -> None:
    spam = FakeAnalyzer(analysis(category=LeadCategory.SPAM))
    first = await _ingest(db_session, _email("SEO offer", sender="a@spam.biz"))
    await _run(db_session, first, spam)
    second = await _ingest(db_session, _email("SEO offer", sender="b@spam.biz"))
    await _run(db_session, second, spam)

    assert (await _lead(db_session, second)).possible_duplicate_of_id is None
