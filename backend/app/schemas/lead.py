"""Leads as the dashboard sees them."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.models.enums import (
    Channel,
    DeliveryStatus,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MessageDirection,
    MessageKind,
    NotificationKind,
    Priority,
    ProcessingState,
)
from app.schemas.user import UserSummary


class LeadSort(StrEnum):
    NEWEST = "newest"
    SCORE = "score"
    ACTIVITY = "activity"  # latest customer message first


class OwnerFilter(StrEnum):
    ME = "me"
    UNASSIGNED = "unassigned"


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    email: str | None
    phone_e164: str | None
    company_name: str | None
    company_domain: str | None


class LeadListItem(BaseModel):
    id: uuid.UUID
    source: Channel
    category: LeadCategory | None
    score: int | None
    priority: Priority | None
    status: LeadStatus
    processing_state: ProcessingState
    summary: str | None
    language: str | None
    contact: ContactRead
    owner: UserSummary | None
    created_at: datetime
    last_message_at: datetime | None
    sla_breached: bool


class LeadPage(BaseModel):
    items: list[LeadListItem]
    total: int
    page: int
    page_size: int


class LeadStats(BaseModel):
    new_24h: int
    high_priority_open: int
    unanswered_past_sla: int
    converted_30d: int
    closed_30d: int  # converted + lost, the conversion rate's denominator
    conversion_rate_30d: float | None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: Channel
    direction: MessageDirection
    kind: MessageKind
    sender_address: str
    subject: str | None
    body_text: str
    is_auto_reply: bool
    sent_by_user_id: uuid.UUID | None
    delivery_status: DeliveryStatus
    delivery_error: str | None
    transcription_status: str | None
    occurred_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: LeadEventType
    actor_id: uuid.UUID | None
    payload: dict[str, Any]
    created_at: datetime


class ScoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int
    breakdown: dict[str, Any]
    rationale: str | None
    model: str
    prompt_version: str
    created_at: datetime


class LeadRef(BaseModel):
    id: uuid.UUID
    display_name: str
    status: LeadStatus


class LeadDetail(LeadListItem):
    processing_error: str | None
    first_response_at: datetime | None
    converted_at: datetime | None
    latest_score: ScoreRead | None
    messages: list[MessageRead]
    events: list[EventRead]
    possible_duplicate_of: LeadRef | None
    can_reply: bool
    reply_blocked_reason: str | None


class SimilarLeadRead(BaseModel):
    id: uuid.UUID
    display_name: str
    category: LeadCategory | None
    score: int | None
    status: LeadStatus
    summary: str | None
    similarity: float
    created_at: datetime


class LeadUpdate(BaseModel):
    """Omitted fields are unchanged. `owner_id: null` unassigns."""

    status: LeadStatus | None = None
    owner_id: uuid.UUID | None = None


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ReplyCreate(BaseModel):
    body: Text


class NoteCreate(BaseModel):
    body: Text


def _no_nul(value: str | None) -> str | None:
    # Postgres text can't hold NUL; a pasted one would otherwise be a 500.
    return value.replace("\x00", "") if value else value


class LeadFilters(BaseModel):
    source: Channel | None = None
    category: list[LeadCategory] = Field(default_factory=list)
    priority: list[Priority] = Field(default_factory=list)
    status: list[LeadStatus] = Field(default_factory=list)
    owner: OwnerFilter | uuid.UUID | None = None
    score_min: int | None = Field(default=None, ge=0, le=100)
    score_max: int | None = Field(default=None, ge=0, le=100)
    q: Annotated[str | None, AfterValidator(_no_nul)] = Field(default=None, max_length=200)
    sort: LeadSort = LeadSort.NEWEST
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=25, ge=1, le=100)


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class LeadPreviewRequest(BaseModel):
    """A message to run through the pipeline without storing it ("Try a lead")."""

    channel: Channel
    sender_name: ShortText | None = None
    sender_address: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320)
    ] = Field(description="An email address, or a phone number for WhatsApp")
    subject: ShortText | None = None
    text: Text

    @model_validator(mode="after")
    def _address_fits_channel(self) -> "LeadPreviewRequest":
        if self.channel == Channel.EMAIL and "@" not in self.sender_address:
            raise ValueError("An email lead needs an email address")
        if self.channel == Channel.WHATSAPP and not self.sender_address.lstrip("+").isdigit():
            raise ValueError("A WhatsApp lead needs a phone number, digits only")
        return self


class LeadPreviewRead(BaseModel):
    """What the pipeline would do with the message. Nothing was stored or sent."""

    category: LeadCategory
    category_confidence: float
    language: str
    summary: str
    rationale: str
    company_name: str | None
    job_title: str | None
    score: int
    priority: Priority
    breakdown: dict[str, Any] = Field(description="The same shape as a stored score's breakdown")
    auto_reply: str | None = Field(description="The reply that would be sent, if any")
    alert: NotificationKind | None = Field(description="The alert that would be raised, if any")
    similar: list[SimilarLeadRead]
    model: str
    prompt_version: str
