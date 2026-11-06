"""Aggregations for reports. Every query covers the leads opened in [start, end)."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, Exists, and_, exists, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Lead, LeadEvent, Message, User
from app.models.enums import (
    Channel,
    DeliveryStatus,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MessageDirection,
    Priority,
)

# The funnel's stages in order; a lead that reached a later stage also counts for the earlier.
STAGES = (LeadStatus.CONTACTED, LeadStatus.QUALIFIED, LeadStatus.CONVERTED)
# A person moving a lead to one of these is a response; to spam or lost isn't.
RESPONSE_STATUSES = [s.value for s in STAGES]


@dataclass(frozen=True)
class ResponseRow:
    source: Channel
    priority: Priority | None
    status: LeadStatus
    minutes: float | None  # None: no human response yet
    age_minutes: float  # how long ago the lead arrived


@dataclass(frozen=True)
class TopLead:
    lead: Lead
    contact: Contact
    owner_name: str | None


def _not_spam() -> ColumnElement[bool]:
    return and_(
        Lead.status != LeadStatus.SPAM,
        or_(Lead.category.is_(None), Lead.category != LeadCategory.SPAM),
    )


def _reached(stage: LeadStatus) -> ColumnElement[bool]:
    """The lead is at this stage or a later one now, or was moved there at some point (a
    lead that was qualified and then lost still reached qualified)."""
    later = [s.value for s in STAGES[STAGES.index(stage) :]]
    was_moved: Exists = exists().where(
        LeadEvent.lead_id == Lead.id,
        LeadEvent.type == LeadEventType.STATUS_CHANGED,
        LeadEvent.payload["to"].astext.in_(later),
    )
    return or_(Lead.status.in_(later), was_moved)


def _first_response() -> ColumnElement[Any]:
    """When a person first responded: the earliest of a status change they made to a
    response stage and a reply they sent."""
    by_status = (
        select(func.min(LeadEvent.created_at))
        .where(
            LeadEvent.lead_id == Lead.id,
            LeadEvent.type == LeadEventType.STATUS_CHANGED,
            LeadEvent.actor_id.is_not(None),
            LeadEvent.payload["to"].astext.in_(RESPONSE_STATUSES),
        )
        .correlate(Lead)
        .scalar_subquery()
    )
    by_reply = (
        select(func.min(Message.occurred_at))
        .where(
            Message.lead_id == Lead.id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.sent_by_user_id.is_not(None),
            Message.delivery_status != DeliveryStatus.FAILED,  # a reply that never left
        )
        .correlate(Lead)
        .scalar_subquery()
    )
    # LEAST ignores NULLs, so a lead with only one kind of response still gets a time.
    return func.least(by_status, by_reply)


class ReportQueries:
    def __init__(self, session: AsyncSession, *, start: datetime, end: datetime) -> None:
        self._session = session
        self._in_period = and_(Lead.created_at >= start, Lead.created_at < end)
        self._start = start
        self._end = end

    async def volume(self, timezone: str) -> dict[tuple[date, Channel], int]:
        day = func.date(func.timezone(literal(timezone), Lead.created_at))
        rows = await self._session.execute(
            select(day, Lead.source, func.count()).where(self._in_period).group_by(day, Lead.source)
        )
        return {(d, source): int(n) for d, source, n in rows.all()}

    async def totals(self) -> dict[str, int]:
        row = (
            await self._session.execute(
                select(
                    func.count(),
                    func.count().filter(~_not_spam()),
                    func.count().filter(Lead.source == Channel.EMAIL),
                    func.count().filter(Lead.source == Channel.WHATSAPP),
                ).where(self._in_period)
            )
        ).one()
        return {"total": row[0], "spam": row[1], "email": row[2], "whatsapp": row[3]}

    async def categories(self) -> list[tuple[LeadCategory | None, int]]:
        rows = await self._session.execute(
            select(Lead.category, func.count())
            .where(self._in_period)
            .group_by(Lead.category)
            .order_by(func.count().desc())
        )
        return [(category, int(n)) for category, n in rows.all()]

    async def funnel(self) -> dict[str, int]:
        row = (
            await self._session.execute(
                select(
                    func.count(),
                    func.count().filter(_reached(LeadStatus.CONTACTED)),
                    func.count().filter(_reached(LeadStatus.QUALIFIED)),
                    func.count().filter(_reached(LeadStatus.CONVERTED)),
                    func.count().filter(Lead.status == LeadStatus.LOST),
                ).where(self._in_period, _not_spam())
            )
        ).one()
        keys = ("created", "contacted", "qualified", "converted", "lost")
        return {key: int(value) for key, value in zip(keys, row, strict=True)}

    async def response_rows(self) -> list[ResponseRow]:
        first = _first_response()
        minutes = func.extract("epoch", first - Lead.created_at) / 60
        age = func.extract("epoch", func.now() - Lead.created_at) / 60
        rows = await self._session.execute(
            select(Lead.source, Lead.priority, Lead.status, minutes, age).where(
                self._in_period, _not_spam()
            )
        )
        return [
            ResponseRow(source, priority, status, float(m) if m is not None else None, float(a))
            for source, priority, status, m, a in rows.all()
        ]

    async def timezone_exists(self, name: str) -> bool:
        """Postgres does the day bucketing, so the name must be one Postgres knows."""
        found = await self._session.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = :name)"),
            {"name": name},
        )
        return bool(found)

    async def high_value(self, threshold: int) -> tuple[int, int, list[TopLead]]:
        valuable = and_(self._in_period, _not_spam(), Lead.score >= threshold)
        counts = (
            await self._session.execute(
                select(func.count(), func.count().filter(_reached(LeadStatus.CONVERTED))).where(
                    valuable
                )
            )
        ).one()
        rows = await self._session.execute(
            select(Lead, Contact, User.full_name)
            .join(Contact, Contact.id == Lead.contact_id)
            .outerjoin(User, User.id == Lead.owner_id)
            .where(valuable)
            .order_by(Lead.score.desc(), Lead.created_at.desc())
            .limit(10)
        )
        top = [TopLead(lead, contact, owner) for lead, contact, owner in rows.all()]
        return int(counts[0]), int(counts[1]), top

    async def auto_replies_sent(self) -> int:
        sent = await self._session.scalar(
            select(func.count())
            .select_from(Message)
            .join(Lead, Lead.id == Message.lead_id)
            .where(
                self._in_period,
                Message.is_auto_reply,
                Message.delivery_status.in_(
                    [DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.READ]
                ),
            )
        )
        return int(sent or 0)
