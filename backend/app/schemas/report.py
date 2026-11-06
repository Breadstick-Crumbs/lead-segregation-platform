"""The reporting screen: one document per period (docs/REPORTING.md)."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.models.enums import Channel, LeadCategory, LeadStatus, Priority


class ReportPeriod(BaseModel):
    start: datetime
    end: datetime
    days: int
    timezone: str


class VolumeDay(BaseModel):
    date: date
    email: int
    whatsapp: int


class CategoryCount(BaseModel):
    category: LeadCategory | None  # None: not classified (yet, or scoring failed)
    count: int


class Funnel(BaseModel):
    """Non-spam leads opened in the period, by the furthest stage each one reached."""

    created: int
    contacted: int
    qualified: int
    converted: int
    lost: int
    conversion_rate: float | None


class SourceResponse(BaseModel):
    source: Channel
    responded: int
    median_minutes: float | None


class ResponseTimes(BaseModel):
    """Time from a lead arriving to the first human response: a reply, or a person moving
    it to contacted, qualified or converted. Marking a lead spam or lost isn't a response."""

    responded: int
    unanswered: int
    median_minutes: float | None
    p90_minutes: float | None
    sla_minutes: int
    urgent: int  # high and critical leads whose SLA outcome is known (see the service)
    urgent_within_sla: int
    within_sla_rate: float | None
    by_source: list[SourceResponse]


class HighValueLead(BaseModel):
    id: uuid.UUID
    display_name: str
    score: int
    priority: Priority | None
    category: LeadCategory | None
    status: LeadStatus
    owner_name: str | None
    created_at: datetime


class HighValue(BaseModel):
    threshold: int  # the "high" priority threshold
    count: int
    converted: int
    conversion_rate: float | None
    share_of_leads: float | None  # of the genuine (non-spam) leads in the period
    top: list[HighValueLead]


class Report(BaseModel):
    period: ReportPeriod
    total: int  # every lead opened in the period, spam included
    spam: int
    by_source: dict[Channel, int]
    auto_replies_sent: int
    volume: list[VolumeDay]
    categories: list[CategoryCount]
    funnel: Funnel
    response: ResponseTimes
    high_value: HighValue
