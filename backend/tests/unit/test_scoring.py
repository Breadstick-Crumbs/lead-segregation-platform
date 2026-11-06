import pytest

from app.models.enums import LeadCategory, Priority
from app.schemas.app_settings import (
    KeywordRule,
    ScoringKeywords,
    ScoringThresholds,
    ScoringWeights,
)
from app.services.scoring import (
    History,
    RuleSignals,
    ScoreResult,
    company_factor,
    history_factor,
    keyword_factor,
    priority_for,
    score_lead,
)
from tests.fakes.ai import analysis

RULES = ScoringKeywords().rules


def _score(
    text: str = "", *, corporate: bool = True, history: History | None = None, **kw: object
) -> ScoreResult:
    return score_lead(
        analysis(**kw),
        RuleSignals(text=text, corporate_domain=corporate, history=history or History()),
        weights=ScoringWeights(),
        thresholds=ScoringThresholds(),
        keywords=RULES,
    )


def test_keywords_match_whole_words_case_insensitively() -> None:
    points, matched = keyword_factor("Could you send PRICING and a Quote?", RULES)

    assert matched == ["pricing", "quote"]
    assert points == 50


def test_keywords_do_not_match_inside_other_words() -> None:
    assert keyword_factor("We are demolishing the old office", RULES) == (0, [])


def test_arabic_keywords_match_with_attached_prefixes() -> None:
    # "والسعر" is "and the price": the conjunction is written attached to the word.
    points, matched = keyword_factor("أريد عرض سعر والسعر النهائي", RULES)

    assert matched == ["عرض سعر", "السعر"]
    assert points == 45


def test_keyword_points_are_capped_at_100() -> None:
    rules = [KeywordRule(term=t, points=60) for t in ("rfp", "tender")]

    assert keyword_factor("RFP and tender attached", rules) == (100, ["rfp", "tender"])


def test_company_factor_counts_each_signal() -> None:
    assert company_factor(corporate_domain=True, analysis=analysis()) == (
        100,
        ["corporate_domain", "company_name", "job_title"],
    )
    assert company_factor(
        corporate_domain=False, analysis=analysis(company_name=None, job_title=None)
    ) == (0, [])


def test_history_factor_combines_contact_and_similar_leads() -> None:
    points, details = history_factor(
        History(prior_leads=5, prior_conversions=1, similar_converted=1, similar_lost=1)
    )

    # Two prior leads counted (30) + a prior conversion (40) + half of similar converted (15).
    assert points == 85
    assert details["similar_conversion_rate"] == 0.5


def test_history_factor_ignores_similar_leads_without_an_outcome() -> None:
    points, details = history_factor(History())

    assert points == 0
    assert details["similar_conversion_rate"] is None


def test_history_factor_is_capped() -> None:
    points, _ = history_factor(
        History(prior_leads=9, prior_conversions=3, replied_to_us=True, similar_converted=4)
    )

    assert points == 100


def test_score_is_the_weighted_average_of_the_factors() -> None:
    result = _score("Please send a quote", intent=80, urgency=40, business_value=60)

    # (80*25 + 40*15 + 60*20 + 25*15 + 100*15 + 0*10) / 100 = 56.75
    assert result.total == 57
    assert result.priority == Priority.MEDIUM
    assert result.breakdown["keywords"] == {"value": 25, "weight": 15, "matched": ["quote"]}
    assert result.breakdown["cap"] is None


def test_spam_is_capped_whatever_the_signals() -> None:
    result = _score("urgent pricing quote rfp", category=LeadCategory.SPAM, intent=100)

    assert result.total == 5
    assert result.priority == Priority.LOW
    assert result.breakdown["cap"] == {"reason": "spam", "max": 5}
    assert result.breakdown["weighted"] > 5


def test_support_requests_stay_out_of_sales_priority() -> None:
    result = _score("urgent", category=LeadCategory.SUPPORT_REQUEST, intent=100, urgency=100)

    assert result.total == 40
    assert result.priority == Priority.MEDIUM  # still gets the auto-reply (> 20)


@pytest.mark.parametrize(
    ("score", "priority"),
    [
        (0, Priority.LOW),
        (20, Priority.LOW),  # the spec: reply only when the score is *above* 20
        (21, Priority.MEDIUM),
        (59, Priority.MEDIUM),
        (60, Priority.HIGH),
        (79, Priority.HIGH),
        (80, Priority.CRITICAL),
        (100, Priority.CRITICAL),
    ],
)
def test_priority_bands(score: int, priority: Priority) -> None:
    assert priority_for(score, ScoringThresholds()) == priority


def test_custom_weights_change_the_score() -> None:
    only_intent = ScoringWeights(
        intent=1, urgency=0, business_value=0, keywords=0, company=0, history=0
    )
    result = score_lead(
        analysis(intent=73),
        RuleSignals(text="", corporate_domain=False, history=History()),
        weights=only_intent,
        thresholds=ScoringThresholds(),
        keywords=RULES,
    )

    assert result.total == 73


def test_half_points_round_up_at_the_auto_reply_threshold() -> None:
    # Regression: banker's rounding turned 20.5 into 20, which silently skips the reply.
    weights = ScoringWeights(
        intent=1, urgency=1, business_value=0, keywords=0, company=0, history=0
    )
    result = score_lead(
        analysis(intent=20, urgency=21),
        RuleSignals(text="", corporate_domain=False, history=History()),
        weights=weights,
        thresholds=ScoringThresholds(),
        keywords=RULES,
    )

    assert result.total == 21
    assert result.priority == Priority.MEDIUM
