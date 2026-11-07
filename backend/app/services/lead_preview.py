"""A dry run of the AI pipeline on a message typed into the dashboard ("Try a lead").

The same classifier, embedding, similar-lead search, scoring, auto-reply template and alert
rules as a real lead, but nothing is stored, sent or assigned. It is the one place OpenAI is
called inside a request: a person is waiting for the answer, so it can't be a job. The
endpoint is rate limited, and the OpenAI calls happen before any database work, so no
transaction stays open while they run (as in `LeadPipeline`).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.integrations.openai_llm import AIError, Embedder, LeadAnalyzer, embedding_text
from app.models.enums import Channel, LeadCategory, LeadStatus, NotificationKind
from app.repositories.lead_queries import LeadQueries
from app.repositories.leads import LeadRepository
from app.schemas.analysis import ConversationMessage, LeadAnalysis, LeadContext
from app.schemas.app_settings import (
    AutoReplyTemplates,
    ScoringKeywords,
    ScoringThresholds,
    ScoringWeights,
    SimilaritySettings,
)
from app.services.app_settings import load_settings
from app.services.auto_reply import render_reply, wants_auto_reply
from app.services.ingestion import company_domain
from app.services.leads import SIMILAR_LIMIT, SimilarLeadData
from app.services.notifications import alert_for_priority
from app.services.scoring import History, RuleSignals, ScoreResult, score_lead


class AINotConfiguredError(AppError):
    status_code = 503
    code = "ai_not_configured"


class AIUnavailableError(AppError):
    status_code = 502
    code = "ai_unavailable"


@dataclass(frozen=True)
class PreviewInput:
    channel: Channel
    sender_name: str | None
    sender_address: str
    subject: str | None
    text: str


@dataclass(frozen=True)
class LeadPreview:
    analysis: LeadAnalysis
    score: ScoreResult
    auto_reply: str | None  # what would be sent, or None when it wouldn't be
    alert: NotificationKind | None
    similar: list[SimilarLeadData]
    model: str


class LeadPreviewService:
    def __init__(
        self, session: AsyncSession, analyzer: LeadAnalyzer | None, embedder: Embedder | None
    ) -> None:
        self._session = session
        self._analyzer = analyzer
        self._embedder = embedder

    async def preview(self, data: PreviewInput) -> LeadPreview:
        if self._analyzer is None or self._embedder is None:
            raise AINotConfiguredError("OpenAI is not configured on this server")
        is_email = data.channel == Channel.EMAIL
        context = LeadContext(
            channel=data.channel,
            sender_name=data.sender_name,
            sender_address=data.sender_address,
            company_domain=company_domain(data.sender_address) if is_email else None,
            messages=(
                ConversationMessage(
                    sent_at=datetime.now(UTC),
                    subject=data.subject if is_email else None,
                    text=data.text,
                ),
            ),
        )
        try:
            analysis = await self._analyzer.analyze(context)
            embedding = await self._embedder.embed(embedding_text(context))
        except AIError as exc:  # its message is already safe to show (see `_api_error`)
            raise AIUnavailableError(f"The AI service couldn't process this: {exc}") from exc

        similarity = await load_settings(self._session, SimilaritySettings)
        neighbours = await LeadRepository(self._session).similar(
            embedding,
            exclude=None,
            limit=similarity.similar_limit,
            min_similarity=similarity.similar_min,
        )
        found = await LeadQueries(self._session).leads_with_contacts(
            [n.lead_id for n in neighbours]
        )
        thresholds = await load_settings(self._session, ScoringThresholds)
        result = score_lead(
            analysis,
            RuleSignals(
                text=embedding_text(context),
                corporate_domain=context.company_domain is not None,
                # A first message from a new contact: only similar past leads count.
                history=History(
                    similar_converted=sum(
                        1 for n in neighbours if n.status == LeadStatus.CONVERTED
                    ),
                    similar_lost=sum(1 for n in neighbours if n.status == LeadStatus.LOST),
                ),
            ),
            weights=await load_settings(self._session, ScoringWeights),
            thresholds=thresholds,
            keywords=(await load_settings(self._session, ScoringKeywords)).rules,
        )
        auto_reply = (
            render_reply(
                await load_settings(self._session, AutoReplyTemplates),
                language=analysis.language,
                name=data.sender_name,
            )
            if wants_auto_reply(result.total, analysis.category, thresholds)
            else None
        )
        spam = analysis.category == LeadCategory.SPAM
        return LeadPreview(
            analysis=analysis,
            score=result,
            auto_reply=auto_reply,
            alert=None if spam else alert_for_priority(result.priority),
            similar=[
                SimilarLeadData(*found[n.lead_id], n.similarity)
                for n in neighbours[:SIMILAR_LIMIT]
                if n.lead_id in found
            ],
            model=self._analyzer.model,
        )
