"""The AI classifier's contract: what it reads (`LeadContext`) and what it must return
(`LeadAnalysis`, enforced as an OpenAI structured-output schema and validated again here)."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.models.enums import Channel, LeadCategory


def _fit(limit: int) -> AfterValidator:
    # Length limits are applied here, not in the JSON schema: structured outputs don't
    # accept string length keywords.
    return AfterValidator(lambda value: value.strip()[:limit])


def _optional_fit(limit: int) -> AfterValidator:
    return AfterValidator(lambda value: (value.strip()[:limit] or None) if value else None)


def _language(value: str) -> str:
    code = re.sub(r"[^a-z]", "", value.strip().lower()[:2])
    return code if len(code) == 2 else "en"  # noqa: PLR2004  (ISO 639-1 codes are two letters)


class LeadAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: LeadCategory
    category_confidence: float = Field(ge=0, le=1, description="0 to 1")
    language: Annotated[str, AfterValidator(_language)] = Field(
        description="ISO 639-1 code of the language the customer wrote in, e.g. en or ar"
    )
    intent: int = Field(ge=0, le=100, description="How clearly they want to buy or engage")
    urgency: int = Field(ge=0, le=100, description="Timeline pressure")
    business_value: int = Field(ge=0, le=100, description="Deal size, seats or budget signals")
    company_name: Annotated[str | None, _optional_fit(200)] = Field(
        description="The sender's company, if stated or clear from a signature; otherwise null"
    )
    job_title: Annotated[str | None, _optional_fit(120)] = Field(
        description="The sender's job title from a signature or introduction; otherwise null"
    )
    summary: Annotated[str, _fit(300)] = Field(
        description="One line for the sales dashboard, in English"
    )
    rationale: Annotated[str, _fit(600)] = Field(
        description="Two or three sentences explaining the category and scores, in English"
    )


@dataclass(frozen=True)
class ConversationMessage:
    sent_at: datetime
    subject: str | None
    text: str


@dataclass(frozen=True)
class LeadContext:
    """Everything the classifier sees about one lead. Message text is untrusted."""

    channel: Channel
    sender_name: str | None
    sender_address: str
    company_domain: str | None
    messages: tuple[ConversationMessage, ...]
