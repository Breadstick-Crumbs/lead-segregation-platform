import pytest
from pydantic import ValidationError

from app.schemas.app_settings import (
    DEFAULT_SETTINGS,
    AutoReplyTemplates,
    ScoringThresholds,
    ScoringWeights,
)


def test_default_weights_follow_the_documented_split() -> None:
    weights = ScoringWeights()

    assert weights.model_dump() == {
        "intent": 25,
        "urgency": 15,
        "business_value": 20,
        "keywords": 15,
        "company": 15,
        "history": 10,
    }


def test_default_thresholds_match_the_spec() -> None:
    thresholds = ScoringThresholds()

    assert thresholds.auto_reply_above == 20
    assert (thresholds.high_at, thresholds.critical_at) == (60, 80)


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        ScoringThresholds(high_at=90, critical_at=80)


def test_weights_cannot_all_be_zero() -> None:
    with pytest.raises(ValidationError):
        ScoringWeights(intent=0, urgency=0, business_value=0, keywords=0, company=0, history=0)


def test_templates_need_the_default_language() -> None:
    with pytest.raises(ValidationError):
        AutoReplyTemplates(default_language="fr")


def test_every_default_has_a_unique_key() -> None:
    keys = [model.KEY for model in DEFAULT_SETTINGS]

    assert len(keys) == len(set(keys))
