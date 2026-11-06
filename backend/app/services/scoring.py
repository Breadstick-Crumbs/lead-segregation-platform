"""The hybrid lead score (docs/SCORING.md). Pure functions: the LLM supplies intent, urgency
and business value, and the rules below supply keywords, company and history.

    score = round_half_up(sum(factor * weight) / sum(weights)), then category caps."""

import math
import re
from dataclasses import dataclass
from typing import Any

from app.models.enums import LeadCategory, Priority
from app.schemas.analysis import LeadAnalysis
from app.schemas.app_settings import KeywordRule, ScoringThresholds, ScoringWeights

MAX_FACTOR = 100

# Company factor: how much we know about the organisation behind the lead.
CORPORATE_DOMAIN_POINTS = 40
COMPANY_NAME_POINTS = 35
JOB_TITLE_POINTS = 25

# History factor: what this contact and similar leads did before.
PRIOR_LEAD_POINTS = 15  # per earlier lead from this contact
PRIOR_LEADS_COUNTED = 2
PRIOR_CONVERSION_POINTS = 40
REPLIED_TO_US_POINTS = 20
SIMILAR_CONVERSION_POINTS = 30  # scaled by the conversion rate of similar decided leads


@dataclass(frozen=True)
class History:
    prior_leads: int = 0
    prior_conversions: int = 0
    replied_to_us: bool = False
    # Of the similar past leads (pgvector neighbours) that reached an outcome, how many
    # converted and how many were lost.
    similar_converted: int = 0
    similar_lost: int = 0


@dataclass(frozen=True)
class RuleSignals:
    text: str
    corporate_domain: bool
    history: History


@dataclass(frozen=True)
class ScoreResult:
    total: int
    priority: Priority
    breakdown: dict[str, Any]


def _pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term.casefold())
    if term.isascii():
        return re.compile(rf"(?<!\w){escaped}(?!\w)")
    # Arabic attaches prefixes such as و and ال to the word, so match inside words too.
    return re.compile(escaped)


def keyword_factor(text: str, rules: list[KeywordRule]) -> tuple[int, list[str]]:
    """Points of every distinct term present, capped at 100."""
    folded = text.casefold()
    matched = [rule for rule in rules if _pattern(rule.term).search(folded)]
    points = sum(rule.points for rule in matched)
    return min(points, MAX_FACTOR), [rule.term for rule in matched]


def company_factor(*, corporate_domain: bool, analysis: LeadAnalysis) -> tuple[int, list[str]]:
    signals = {
        "corporate_domain": (corporate_domain, CORPORATE_DOMAIN_POINTS),
        "company_name": (analysis.company_name is not None, COMPANY_NAME_POINTS),
        "job_title": (analysis.job_title is not None, JOB_TITLE_POINTS),
    }
    present = [name for name, (found, _) in signals.items() if found]
    return sum(points for found, points in signals.values() if found), present


def history_factor(history: History) -> tuple[int, dict[str, Any]]:
    points = min(history.prior_leads, PRIOR_LEADS_COUNTED) * PRIOR_LEAD_POINTS
    if history.prior_conversions:
        points += PRIOR_CONVERSION_POINTS
    if history.replied_to_us:
        points += REPLIED_TO_US_POINTS
    decided = history.similar_converted + history.similar_lost
    rate = history.similar_converted / decided if decided else None
    if rate is not None:
        points += round(SIMILAR_CONVERSION_POINTS * rate)
    details = {
        "prior_leads": history.prior_leads,
        "prior_conversions": history.prior_conversions,
        "replied_to_us": history.replied_to_us,
        "similar_decided": decided,
        "similar_conversion_rate": None if rate is None else round(rate, 2),
    }
    return min(points, MAX_FACTOR), details


def priority_for(score: int, thresholds: ScoringThresholds) -> Priority:
    if score <= thresholds.auto_reply_above:
        return Priority.LOW
    if score < thresholds.high_at:
        return Priority.MEDIUM
    if score < thresholds.critical_at:
        return Priority.HIGH
    return Priority.CRITICAL


def score_lead(
    analysis: LeadAnalysis,
    signals: RuleSignals,
    *,
    weights: ScoringWeights,
    thresholds: ScoringThresholds,
    keywords: list[KeywordRule],
) -> ScoreResult:
    keyword_points, matched = keyword_factor(signals.text, keywords)
    company_points, company_signals = company_factor(
        corporate_domain=signals.corporate_domain, analysis=analysis
    )
    history_points, history_details = history_factor(signals.history)

    factors: dict[str, dict[str, Any]] = {
        "intent": {"value": analysis.intent, "weight": weights.intent},
        "urgency": {"value": analysis.urgency, "weight": weights.urgency},
        "business_value": {"value": analysis.business_value, "weight": weights.business_value},
        "keywords": {"value": keyword_points, "weight": weights.keywords, "matched": matched},
        "company": {"value": company_points, "weight": weights.company, "signals": company_signals},
        "history": {"value": history_points, "weight": weights.history, **history_details},
    }
    total_weight = sum(factor["weight"] for factor in factors.values())  # > 0, validated
    raw = sum(f["value"] * f["weight"] for f in factors.values()) / total_weight
    weighted = math.floor(raw + 0.5)  # half up: 20.5 scores 21 and gets the auto-reply

    cap: dict[str, Any] | None = None
    if analysis.category == LeadCategory.SPAM:
        cap = {"reason": "spam", "max": thresholds.spam_score_cap}
    elif analysis.category == LeadCategory.SUPPORT_REQUEST:
        cap = {"reason": "support_request", "max": thresholds.support_score_cap}
    total = min(weighted, cap["max"]) if cap else weighted

    breakdown = {
        **factors,
        "weighted": weighted,
        "cap": cap,
        "category": {"value": analysis.category, "confidence": analysis.category_confidence},
    }
    return ScoreResult(total=total, priority=priority_for(total, thresholds), breakdown=breakdown)
