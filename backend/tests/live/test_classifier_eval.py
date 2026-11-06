"""Live evaluation of the classifier against the labelled set. Calls the real OpenAI API,
so it is off by default; run it with `uv run pytest -m live_llm` and OPENAI_API_KEY set."""

import asyncio

import pytest

from app.core.config import get_settings
from app.integrations.openai_llm import PROMPT_VERSION, OpenAILeadAnalyzer
from app.models.enums import LeadCategory
from app.schemas.analysis import LeadAnalysis
from app.schemas.app_settings import ScoringKeywords, ScoringThresholds, ScoringWeights
from app.services.scoring import History, RuleSignals, score_lead
from tests.fixtures.leads import LabelledLead, load_cases

pytestmark = pytest.mark.live_llm

MIN_CATEGORY_ACCURACY = 0.85
CONCURRENCY = 4


async def _classify_all() -> list[tuple[LabelledLead, LeadAnalysis]]:
    settings = get_settings()
    if settings.openai_api_key is None:
        pytest.skip("OPENAI_API_KEY is not set")
    analyzer = OpenAILeadAnalyzer(settings)
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(case: LabelledLead) -> tuple[LabelledLead, LeadAnalysis]:
        async with limit:
            return case, await analyzer.analyze(case.context)

    return list(await asyncio.gather(*(one(case) for case in load_cases())))


def _score(case: LabelledLead, analysis: LeadAnalysis) -> int:
    text = "\n".join(f"{m.subject or ''}\n{m.text}" for m in case.context.messages)
    return score_lead(
        analysis,
        RuleSignals(
            text=text,
            corporate_domain=case.context.company_domain is not None,
            history=History(),
        ),
        weights=ScoringWeights(),
        thresholds=ScoringThresholds(),
        keywords=ScoringKeywords().rules,
    ).total


async def test_classifier_meets_the_bar_on_the_labelled_set() -> None:
    results = await _classify_all()

    report = []
    correct = 0
    for case, analysis in results:
        score = _score(case, analysis)
        ok = analysis.category == case.category
        correct += ok
        report.append(
            f"{'OK ' if ok else 'BAD'} {case.id}: {analysis.category} "
            f"(want {case.category}) score={score} lang={analysis.language}"
        )
    print(f"\nprompt {PROMPT_VERSION}\n" + "\n".join(report))

    accuracy = correct / len(results)
    assert accuracy >= MIN_CATEGORY_ACCURACY, f"category accuracy {accuracy:.0%}"
    for case, analysis in results:
        score = _score(case, analysis)
        assert case.min_score <= score <= case.max_score, (case.id, score)
        assert analysis.language == case.language, (case.id, analysis.language)
        if case.id == "spam-injection":
            # The embedded instructions must not have worked.
            assert analysis.category == LeadCategory.SPAM
