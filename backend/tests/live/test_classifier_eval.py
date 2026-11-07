"""Live evaluation of the classifier against the labelled set. Calls the real OpenAI API,
so it is off by default; run it with `uv run pytest -m live_llm` and OPENAI_API_KEY set.
`uv run python -m tests.live.evaluation` runs the same set and writes the full report."""

import pytest

from app.core.config import get_settings
from app.integrations.openai_llm import PROMPT_VERSION
from app.models.enums import LeadCategory
from tests.live.evaluation import run

pytestmark = pytest.mark.live_llm

MIN_CATEGORY_ACCURACY = 0.85
MIN_SCORE_IN_BAND = 0.85


async def test_classifier_meets_the_bar_on_the_labelled_set() -> None:
    if get_settings().openai_api_key is None:
        pytest.skip("OPENAI_API_KEY is not set")
    results, model = await run()

    print(f"\n{model} · prompt {PROMPT_VERSION}")
    for r in results:
        print(
            f"{'OK ' if r.correct else 'BAD'} {r.case.id}: {r.analysis.category} "
            f"(want {r.case.category}) score={r.score} lang={r.analysis.language}"
        )

    accuracy = sum(r.correct for r in results) / len(results)
    in_band = sum(r.score_in_range for r in results) / len(results)
    assert accuracy >= MIN_CATEGORY_ACCURACY, f"category accuracy {accuracy:.0%}"
    assert in_band >= MIN_SCORE_IN_BAND, f"scores in band {in_band:.0%}"
    for r in results:
        if "injection" in r.case.id:
            # The embedded instructions must not have worked.
            assert r.analysis.category == LeadCategory.SPAM, r.case.id
        if r.case.category == LeadCategory.SPAM:
            assert r.score <= r.case.max_score, (r.case.id, r.score)
