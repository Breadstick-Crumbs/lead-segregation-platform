"""The AI pipeline for one lead: classify, embed, score, flag duplicates, assign.

The run is split so no transaction stays open during OpenAI calls:
1. **Read** a snapshot of the conversation, then end the transaction.
2. **Call** the classifier and the embedding model.
3. **Write** under a row lock, and only if nothing new arrived meanwhile. A follow-up during
   step 2 puts the lead back to `pending` and queues another run, which sees the whole
   conversation; this run's result is then stale, so it is discarded (`SUPERSEDED`).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.openai_llm import (
    PROMPT_VERSION,
    AIError,
    Embedder,
    LeadAnalyzer,
    embedding_text,
)
from app.models import Contact, Lead, LeadScore, Message
from app.models.enums import (
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MessageDirection,
    ProcessingState,
)
from app.repositories.leads import CLOSED_STATUSES, LeadRepository, MessageRepository
from app.repositories.users import UserRepository
from app.schemas.analysis import ConversationMessage, LeadAnalysis, LeadContext
from app.schemas.app_settings import (
    ScoringKeywords,
    ScoringThresholds,
    ScoringWeights,
    SimilaritySettings,
)
from app.services.app_settings import load_settings
from app.services.auto_reply import enqueue_auto_reply, wants_auto_reply
from app.services.lead_queue import has_waiting_job
from app.services.notifications import alert_for_priority, enqueue_alert
from app.services.scoring import History, RuleSignals, ScoreResult, score_lead

logger = get_logger(__name__)

ERROR_CHARS = 2000


class Outcome(StrEnum):
    PROCESSED = "processed"
    SKIPPED = "skipped"  # nothing to do: gone, closed, already processed or still transcribing
    SUPERSEDED = "superseded"  # a newer message arrived; its own run takes over
    FAILED = "failed"


@dataclass(frozen=True)
class _Snapshot:
    lead_id: uuid.UUID
    contact_id: uuid.UUID
    context: LeadContext
    inbound_count: int  # the watermark the write step re-checks
    replied_to_us: bool


class LeadPipeline:
    def __init__(
        self,
        session: AsyncSession,
        analyzer: LeadAnalyzer | None,
        embedder: Embedder | None,
    ) -> None:
        """`analyzer`/`embedder` are None when OpenAI isn't configured: the lead is then
        marked failed straight away, since retrying can't help."""
        self._session = session
        self._analyzer = analyzer
        self._embedder = embedder
        self._leads = LeadRepository(session)
        self._messages = MessageRepository(session)

    async def process(self, lead_id: uuid.UUID, *, final_attempt: bool) -> Outcome:
        snapshot = await self._snapshot(lead_id)
        await self._session.rollback()  # end the read transaction before calling out
        if snapshot is None:
            return Outcome.SKIPPED

        try:
            if self._analyzer is None or self._embedder is None:
                raise AIError("OpenAI is not configured", retryable=False)
            analysis = await self._analyzer.analyze(snapshot.context)
            embedding = await self._embedder.embed(embedding_text(snapshot.context))
            return await self._save(snapshot, analysis, embedding)
        except Exception as exc:
            return await self._handle_error(snapshot, exc, final_attempt=final_attempt)

    async def _handle_error(
        self, snapshot: _Snapshot, exc: Exception, *, final_attempt: bool
    ) -> Outcome:
        """Retry while it can help; otherwise (or on the last attempt) fail the lead, so it is
        never left stuck at `pending`. Unexpected errors (bugs, a deadlock in the write step)
        are retried like transient ones."""
        await self._session.rollback()
        expected = isinstance(exc, AIError)
        retryable = exc.retryable if isinstance(exc, AIError) else True
        if retryable and not final_attempt:
            return await self._retry(snapshot, exc)
        if not expected:
            logger.exception("lead_pipeline_crashed", lead_id=str(snapshot.lead_id))
        return await self._fail(snapshot, str(exc) if expected else f"{type(exc).__name__}: {exc}")

    async def _retry(self, snapshot: _Snapshot, exc: Exception) -> Outcome:
        """Re-raise so procrastinate retries, unless a newer job for this lead is already
        queued (a follow-up arrived): that job covers it, and a retry would clash with its
        queueing lock."""
        if await has_waiting_job(self._session, snapshot.lead_id):
            await self._session.rollback()
            logger.info("lead_pipeline_retry_superseded", lead_id=str(snapshot.lead_id))
            return Outcome.SUPERSEDED
        await self._session.rollback()
        logger.warning("lead_pipeline_retry", lead_id=str(snapshot.lead_id), error=str(exc))
        raise exc

    async def _snapshot(self, lead_id: uuid.UUID) -> _Snapshot | None:
        lead = await self._leads.get(lead_id)
        if (
            lead is None
            or lead.status in CLOSED_STATUSES
            or lead.processing_state != ProcessingState.PENDING
        ):
            return None
        if await self._leads.has_pending_transcription(lead_id):
            return None  # transcribe_message queues this lead again when it finishes
        messages = await self._messages.for_lead(lead_id)
        inbound = [m for m in messages if m.direction == MessageDirection.INBOUND]
        if not inbound:
            return None
        contact = await self._session.get(Contact, lead.contact_id)
        if contact is None:  # FK with ON DELETE RESTRICT: can't happen
            return None
        return _Snapshot(
            lead_id=lead.id,
            contact_id=lead.contact_id,
            context=LeadContext(
                channel=lead.source,
                sender_name=contact.name,
                sender_address=inbound[-1].sender_address,
                company_domain=contact.company_domain,
                messages=tuple(_conversation_message(m) for m in inbound),
            ),
            inbound_count=len(inbound),
            replied_to_us=_replied_to_us(messages),
        )

    async def _current(self, lead: Lead | None, snapshot: _Snapshot) -> Lead | None:
        """`lead` if the snapshot still describes it, else None."""
        if (
            lead is None
            or lead.status in CLOSED_STATUSES
            or lead.processing_state != ProcessingState.PENDING
        ):
            return None
        messages = await self._messages.for_lead(lead.id)
        inbound = sum(1 for m in messages if m.direction == MessageDirection.INBOUND)
        return lead if inbound == snapshot.inbound_count else None

    async def _save(
        self, snapshot: _Snapshot, analysis: LeadAnalysis, embedding: list[float]
    ) -> Outcome:
        # Same lock order as ingestion (contact, then lead), so the two can't deadlock.
        await self._session.get(Contact, snapshot.contact_id, with_for_update=True)
        locked = await self._leads.get(snapshot.lead_id, for_update=True)
        lead = await self._current(locked, snapshot)
        if lead is None:
            await self._session.rollback()
            logger.info("lead_pipeline_superseded", lead_id=str(snapshot.lead_id))
            return Outcome.SUPERSEDED

        similarity = await load_settings(self._session, SimilaritySettings)
        neighbours = await self._leads.similar(
            embedding,
            exclude=lead.id,
            limit=similarity.similar_limit,
            min_similarity=similarity.similar_min,
        )
        others = [n for n in neighbours if n.contact_id != lead.contact_id]
        prior_leads, prior_conversions = await self._leads.contact_history(lead)
        history = History(
            prior_leads=prior_leads,
            prior_conversions=prior_conversions,
            replied_to_us=snapshot.replied_to_us,
            similar_converted=sum(1 for n in others if n.status == LeadStatus.CONVERTED),
            similar_lost=sum(1 for n in others if n.status == LeadStatus.LOST),
        )
        thresholds = await load_settings(self._session, ScoringThresholds)
        result = score_lead(
            analysis,
            RuleSignals(
                text=embedding_text(snapshot.context),
                corporate_domain=snapshot.context.company_domain is not None,
                history=history,
            ),
            weights=await load_settings(self._session, ScoringWeights),
            thresholds=thresholds,
            keywords=(await load_settings(self._session, ScoringKeywords)).rules,
        )

        # Spam campaigns send one text to many people; flagging those helps nobody.
        if lead.possible_duplicate_of_id is None and analysis.category != LeadCategory.SPAM:
            window_start = lead.created_at - timedelta(days=similarity.duplicate_window_days)
            duplicate = next(
                (
                    n
                    for n in others
                    if n.similarity >= similarity.duplicate_min
                    and window_start <= n.created_at <= lead.created_at
                ),
                None,
            )
            if duplicate is not None:
                lead.possible_duplicate_of_id = duplicate.lead_id

        await self._apply(lead, analysis, embedding, result, model=self._model_name())
        # Queued in the same transaction as the score that justifies it.
        if wants_auto_reply(result.total, analysis.category, thresholds) and (
            await self._messages.auto_reply_for(lead.id) is None
        ):
            await enqueue_auto_reply(self._session, lead.id)
        alert = alert_for_priority(result.priority)
        if alert is not None and analysis.category != LeadCategory.SPAM:
            await enqueue_alert(self._session, lead.id, alert)  # raised once per kind
        await self._session.commit()
        logger.info(
            "lead_processed",
            lead_id=str(lead.id),
            category=analysis.category,
            score=result.total,
            priority=result.priority,
        )
        return Outcome.PROCESSED

    def _model_name(self) -> str:
        return self._analyzer.model if self._analyzer is not None else "unknown"

    async def _apply(
        self,
        lead: Lead,
        analysis: LeadAnalysis,
        embedding: list[float],
        result: ScoreResult,
        *,
        model: str,
    ) -> None:
        previous_score = lead.score
        lead.category = analysis.category
        lead.score = result.total
        lead.priority = result.priority
        lead.language = analysis.language
        lead.summary = analysis.summary
        lead.embedding = embedding
        lead.processing_state = ProcessingState.PROCESSED
        lead.processing_error = None

        contact = await self._session.get(Contact, lead.contact_id)
        if contact is not None and contact.company_name is None and analysis.company_name:
            contact.company_name = analysis.company_name

        self._session.add(
            LeadScore(
                lead_id=lead.id,
                total=result.total,
                breakdown=result.breakdown,
                rationale=analysis.rationale,
                model=model,
                prompt_version=PROMPT_VERSION,
            )
        )
        self._leads.add_event(
            lead.id,
            LeadEventType.CLASSIFIED,
            {
                "category": analysis.category,
                "confidence": analysis.category_confidence,
                "language": analysis.language,
                "prompt_version": PROMPT_VERSION,
                "possible_duplicate_of": (
                    str(lead.possible_duplicate_of_id) if lead.possible_duplicate_of_id else None
                ),
            },
        )
        self._leads.add_event(
            lead.id,
            LeadEventType.SCORED,
            {"score": result.total, "priority": result.priority, "previous": previous_score},
        )

        if lead.owner_id is None and analysis.category != LeadCategory.SPAM:
            rep = await UserRepository(self._session).next_round_robin_rep()
            if rep is not None:
                lead.owner_id = rep.id
                rep.last_assigned_at = datetime.now(UTC)
                self._leads.add_event(
                    lead.id,
                    LeadEventType.ASSIGNED,
                    {"owner_id": str(rep.id), "rule": "round_robin"},
                )

    async def _fail(self, snapshot: _Snapshot, error: str) -> Outcome:
        await self._session.rollback()
        locked = await self._leads.get(snapshot.lead_id, for_update=True)
        lead = await self._current(locked, snapshot)
        if lead is None:
            # A newer message re-queued the lead; that run gets its own chance.
            await self._session.rollback()
            return Outcome.SUPERSEDED
        lead.processing_state = ProcessingState.FAILED
        lead.processing_error = error[:ERROR_CHARS]
        self._leads.add_event(lead.id, LeadEventType.PROCESSING_FAILED, {"error": error[:500]})
        await self._session.commit()
        logger.error("lead_pipeline_failed", lead_id=str(lead.id), error=error[:500])
        return Outcome.FAILED


def _conversation_message(message: Message) -> ConversationMessage:
    text = message.body_text or f"[{message.kind} message without text]"
    return ConversationMessage(sent_at=message.occurred_at, subject=message.subject, text=text)


def _replied_to_us(messages: Sequence[Message]) -> bool:
    """The customer wrote after one of our people replied (not just an auto-reply)."""
    seen_reply = False
    for message in messages:
        if message.direction == MessageDirection.OUTBOUND and not message.is_auto_reply:
            seen_reply = True
        elif message.direction == MessageDirection.INBOUND and seen_reply:
            return True
    return False
