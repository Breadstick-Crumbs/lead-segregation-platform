"""Stand-ins for the OpenAI classifier and embedder."""

from collections.abc import Awaitable, Callable

from app.integrations.openai_llm import AIError
from app.models.enums import LeadCategory
from app.models.lead import EMBEDDING_DIMENSIONS
from app.schemas.analysis import LeadAnalysis, LeadContext


def analysis(**overrides: object) -> LeadAnalysis:
    values: dict[str, object] = {
        "category": LeadCategory.SALES_OPPORTUNITY,
        "category_confidence": 0.92,
        "language": "en",
        "intent": 85,
        "urgency": 60,
        "business_value": 70,
        "company_name": "Gulf Build LLC",
        "job_title": "Procurement Manager",
        "summary": "Wants a quote for 40 seats",
        "rationale": "Explicit pricing request with a team-sized deal.",
    }
    values.update(overrides)
    return LeadAnalysis.model_validate(values)


def unit_vector(axis: int, *, tilt: float = 0.0) -> list[float]:
    """A unit-ish vector along `axis`; `tilt` leans it towards the next axis, so two vectors
    with a small tilt difference are very similar but not identical."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[axis] = 1.0
    vector[(axis + 1) % EMBEDDING_DIMENSIONS] = tilt
    return vector


class FakeAnalyzer:
    model = "fake-model"

    def __init__(
        self,
        result: LeadAnalysis | None = None,
        *,
        error: AIError | Exception | None = None,
        before: Callable[[LeadContext], Awaitable[None]] | None = None,
    ) -> None:
        self.result = result or analysis()
        self.error = error
        self.before = before  # runs mid-call, e.g. to simulate a follow-up arriving
        self.contexts: list[LeadContext] = []

    async def analyze(self, context: LeadContext) -> LeadAnalysis:
        self.contexts.append(context)
        if self.before is not None:
            await self.before(context)
        if self.error is not None:
            raise self.error
        return self.result


class FakeEmbedder:
    """Returns the vector of the first marker found in the text, else `default`."""

    def __init__(
        self, by_marker: dict[str, list[float]] | None = None, *, default: int = 0
    ) -> None:
        self.by_marker = by_marker or {}
        self.default = unit_vector(default)

    async def embed(self, text: str) -> list[float]:
        for marker, vector in self.by_marker.items():
            if marker in text:
                return vector
        return self.default
