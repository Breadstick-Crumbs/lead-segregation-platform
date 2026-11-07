"""Runs the real classifier over the labelled set and measures it. Calls the OpenAI API.

    uv run python -m tests.live.evaluation          # writes docs/CLASSIFIER_EVAL.md

`test_classifier_eval.py` uses the same run to hold the classifier to a minimum bar.
"""

import asyncio
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.integrations.openai_llm import PROMPT_VERSION, OpenAILeadAnalyzer
from app.models.enums import LeadCategory
from app.schemas.analysis import LeadAnalysis
from app.schemas.app_settings import ScoringKeywords, ScoringThresholds, ScoringWeights
from app.services.scoring import History, RuleSignals, score_lead
from tests.fixtures.leads import LabelledLead, load_cases

CONCURRENCY = 4
REPORT = Path(__file__).resolve().parents[3] / "docs" / "CLASSIFIER_EVAL.md"
LABELS = {
    LeadCategory.SALES_OPPORTUNITY: "Sales opportunity",
    LeadCategory.DEMO_REQUEST: "Demo request",
    LeadCategory.PARTNERSHIP_INQUIRY: "Partnership",
    LeadCategory.SUPPORT_REQUEST: "Support",
    LeadCategory.GENERAL_INQUIRY: "General",
    LeadCategory.SPAM: "Spam",
}


@dataclass(frozen=True)
class Result:
    case: LabelledLead
    analysis: LeadAnalysis
    score: int
    seconds: float

    @property
    def correct(self) -> bool:
        return self.analysis.category == self.case.category

    @property
    def score_in_range(self) -> bool:
        return self.case.min_score <= self.score <= self.case.max_score


def score_of(case: LabelledLead, analysis: LeadAnalysis) -> int:
    """The score with default settings and no history, as for a first-time sender."""
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


async def run() -> tuple[list[Result], str]:
    """Classify every labelled lead. Raises if OpenAI isn't configured."""
    settings = get_settings()
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is not set")
    analyzer = OpenAILeadAnalyzer(settings)
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(case: LabelledLead) -> Result:
        async with limit:
            started = time.perf_counter()
            analysis = await analyzer.analyze(case.context)
            return Result(case, analysis, score_of(case, analysis), time.perf_counter() - started)

    return list(await asyncio.gather(*(one(case) for case in load_cases()))), analyzer.model


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "n/a"


def report(results: list[Result], model: str) -> str:
    total = len(results)
    correct = sum(r.correct for r in results)
    lines = [
        "# Classifier evaluation",
        "",
        "How well the lead classifier does on the labelled set "
        "(`backend/tests/fixtures/leads/cases.json`). It runs the real OpenAI model with the "
        "production prompt; scores use the default settings and no contact history. "
        "Regenerate with `uv run python -m tests.live.evaluation` (in `backend/`).",
        "",
        f"- **Run:** {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · model `{model}` "
        f"· prompt `{PROMPT_VERSION}`",
        f"- **Set:** {total} leads: "
        + ", ".join(
            f"{n} {lang}" for lang, n in sorted(Counter(r.case.language for r in results).items())
        )
        + "; "
        + ", ".join(
            f"{n} {ch}"
            for ch, n in sorted(Counter(r.case.context.channel for r in results).items())
        ),
        "",
        "## Headline",
        "",
        "| Measure | Result |",
        "|---------|--------|",
        f"| Category accuracy | **{_pct(correct, total)}** ({correct}/{total}) |",
    ]
    for language in sorted({r.case.language for r in results}):
        subset = [r for r in results if r.case.language == language]
        lines.append(
            f"| Accuracy, `{language}` | {_pct(sum(r.correct for r in subset), len(subset))} "
            f"({sum(r.correct for r in subset)}/{len(subset)}) |"
        )
    language_ok = sum(r.analysis.language == r.case.language for r in results)
    in_range = sum(r.score_in_range for r in results)
    spam = [r for r in results if r.case.category == LeadCategory.SPAM]
    injections = [r for r in results if "injection" in r.case.id]
    seconds = sorted(r.seconds for r in results)
    lines += [
        f"| Language detected correctly | {_pct(language_ok, total)} ({language_ok}/{total}) |",
        f"| Score in the expected band | {_pct(in_range, total)} ({in_range}/{total}) |",
        f"| Spam caught | {_pct(sum(r.correct for r in spam), len(spam))} "
        f"({sum(r.correct for r in spam)}/{len(spam)}) |",
        f"| Prompt injections resisted | {sum(r.correct for r in injections)}/{len(injections)} |",
        f"| Median time per lead | {seconds[len(seconds) // 2]:.1f} s |",
        "",
        "## Per category",
        "",
        "| Category | Precision | Recall | F1 | Leads |",
        "|----------|-----------|--------|----|-------|",
    ]
    for category, label in LABELS.items():
        tp = sum(r.correct and r.case.category == category for r in results)
        predicted = sum(r.analysis.category == category for r in results)
        actual = sum(r.case.category == category for r in results)
        precision = tp / predicted if predicted else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        lines.append(f"| {label} | {precision:.0%} | {recall:.0%} | {f1:.2f} | {actual} |")

    lines += [
        "",
        "## Confusion matrix",
        "",
        "Rows are the labelled category, columns what the model chose.",
        "",
        "| Labelled ↓ / chosen → | " + " | ".join(LABELS.values()) + " |",
        "|---|" + "---|" * len(LABELS),
    ]
    for actual_category, label in LABELS.items():
        row = [
            sum(
                r.case.category == actual_category and r.analysis.category == chosen
                for r in results
            )
            for chosen in LABELS
        ]
        lines.append(f"| **{label}** | " + " | ".join(str(n) if n else "·" for n in row) + " |")

    misses = [r for r in results if not r.correct or not r.score_in_range]
    lines += ["", "## Misses", ""]
    if not misses:
        lines.append("None: every lead got its labelled category and a score in its band.")
    else:
        lines += [
            "| Lead | Labelled | Chosen | Score (band) | Model's reasoning |",
            "|---|---|---|---|---|",
        ]
        for r in misses:
            band = f"{r.case.min_score} to {r.case.max_score}"
            reasoning = r.analysis.rationale.replace("|", "/").replace("\n", " ")
            lines.append(
                f"| `{r.case.id}` | {LABELS[r.case.category]} | {LABELS[r.analysis.category]} "
                f"| {r.score} ({band}) | {reasoning} |"
            )
    lines += [
        "",
        "## Reading this",
        "",
        "- The set is small and hand-labelled, so treat one miss as a prompt to read the case, "
        "not a trend. Some messages are honestly ambiguous (a partner who also wants pricing).",
        "- Category accuracy is what routing depends on. The score band check is looser: it "
        "catches a hot lead scored cold or spam scored warm, not a few points either way.",
        "- Every new labelled case should be added to `cases.json` and this report regenerated "
        "whenever the prompt (`PROMPT_VERSION`) or model changes.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    results, model = asyncio.run(run())
    REPORT.write_text(report(results, model), encoding="utf-8", newline="\n")
    correct = sum(r.correct for r in results)
    print(f"{correct}/{len(results)} correct; report written to {REPORT}")


if __name__ == "__main__":
    main()
