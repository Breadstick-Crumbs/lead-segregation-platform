"""The labelled set itself: well-formed, and covering every category in both languages."""

from collections import Counter

from app.integrations.openai_llm import build_prompt
from app.models.enums import LeadCategory
from tests.fixtures.leads import load_cases


def test_every_category_has_at_least_two_labelled_leads() -> None:
    counts = Counter(case.category for case in load_cases())

    assert all(counts[category] >= 2 for category in LeadCategory), counts


def test_the_set_includes_arabic_and_a_prompt_injection() -> None:
    cases = {case.id: case for case in load_cases()}

    assert any(case.language == "ar" for case in cases.values())
    assert cases["spam-injection"].category == LeadCategory.SPAM


def test_every_case_builds_a_prompt() -> None:
    for case in load_cases():
        assert case.context.messages[0].text in build_prompt(case.context)
        assert 0 <= case.min_score <= case.max_score <= 100
