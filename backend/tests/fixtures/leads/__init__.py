"""Labelled leads for evaluating the classifier (`cases.json`)."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.models.enums import Channel, LeadCategory
from app.schemas.analysis import ConversationMessage, LeadContext
from app.services.ingestion import company_domain

CASES_FILE = Path(__file__).with_name("cases.json")


@dataclass(frozen=True)
class LabelledLead:
    id: str
    context: LeadContext
    category: LeadCategory
    language: str
    min_score: int = 0
    max_score: int = 100


def load_cases() -> list[LabelledLead]:
    cases = []
    for raw in json.loads(CASES_FILE.read_text(encoding="utf-8")):
        channel = Channel(raw["channel"])
        cases.append(
            LabelledLead(
                id=raw["id"],
                context=LeadContext(
                    channel=channel,
                    sender_name=raw["name"],
                    sender_address=raw["sender"],
                    company_domain=(
                        company_domain(raw["sender"]) if channel == Channel.EMAIL else None
                    ),
                    messages=(ConversationMessage(datetime.now(UTC), raw["subject"], raw["body"]),),
                ),
                category=LeadCategory(raw["category"]),
                language=raw["language"],
                min_score=raw.get("min_score", 0),
                max_score=raw.get("max_score", 100),
            )
        )
    return cases
