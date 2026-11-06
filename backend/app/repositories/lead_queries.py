"""Read queries behind the dashboard: the filtered lead list, KPI stats, and a lead's detail."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Exists, ScalarSelect, Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Lead, LeadEvent, LeadScore, Message, User
from app.models.enums import (
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MessageDirection,
    NotificationKind,
    Priority,
)
from app.repositories.leads import CLOSED_STATUSES
from app.schemas.lead import LeadFilters, LeadSort, OwnerFilter


@dataclass(frozen=True)
class LeadRow:
    lead: Lead
    contact: Contact
    owner: User | None
    last_message_at: datetime | None
    sla_breached: bool


def _last_inbound() -> ScalarSelect[datetime]:
    return (
        select(func.max(Message.occurred_at))
        .where(Message.lead_id == Lead.id, Message.direction == MessageDirection.INBOUND)
        .correlate(Lead)
        .scalar_subquery()
    )


def _sla_breached() -> Exists:
    return exists().where(
        LeadEvent.lead_id == Lead.id,
        LeadEvent.type == LeadEventType.ESCALATED,
        LeadEvent.payload["kind"].astext == NotificationKind.SLA_BREACH.value,
    )


def _lost_since(since: datetime) -> Exists:
    """The lead was marked lost on or after `since` (its latest change to lost)."""
    return exists().where(
        LeadEvent.lead_id == Lead.id,
        LeadEvent.type == LeadEventType.STATUS_CHANGED,
        LeadEvent.payload["to"].astext == LeadStatus.LOST.value,
        LeadEvent.created_at >= since,
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class LeadQueries:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _filtered(self, filters: LeadFilters, *, viewer_id: uuid.UUID) -> Select[Lead]:
        statement = select(Lead).join(Contact, Contact.id == Lead.contact_id)
        conditions = []
        if filters.source is not None:
            conditions.append(Lead.source == filters.source)
        if filters.category:
            conditions.append(Lead.category.in_(filters.category))
        if filters.priority:
            conditions.append(Lead.priority.in_(filters.priority))
        if filters.status:
            conditions.append(Lead.status.in_(filters.status))
        if filters.owner == OwnerFilter.ME:
            conditions.append(Lead.owner_id == viewer_id)
        elif filters.owner == OwnerFilter.UNASSIGNED:
            conditions.append(Lead.owner_id.is_(None))
        elif isinstance(filters.owner, uuid.UUID):
            conditions.append(Lead.owner_id == filters.owner)
        if filters.score_min is not None:
            conditions.append(Lead.score >= filters.score_min)
        if filters.score_max is not None:
            conditions.append(Lead.score <= filters.score_max)
        if filters.q and filters.q.strip():
            pattern = f"%{_escape_like(filters.q.strip())}%"
            conditions.append(
                or_(
                    Contact.name.ilike(pattern, escape="\\"),
                    Contact.email.ilike(pattern, escape="\\"),
                    Contact.phone_e164.ilike(pattern, escape="\\"),
                    Contact.company_name.ilike(pattern, escape="\\"),
                    Lead.summary.ilike(pattern, escape="\\"),
                )
            )
        return statement.where(and_(*conditions)) if conditions else statement

    async def page(
        self, filters: LeadFilters, *, viewer_id: uuid.UUID
    ) -> tuple[list[LeadRow], int]:
        base = self._filtered(filters, viewer_id=viewer_id)
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))

        last_message = _last_inbound().label("last_message_at")
        breached = _sla_breached().label("sla_breached")
        order = {
            LeadSort.NEWEST: (Lead.created_at.desc(),),
            LeadSort.SCORE: (Lead.score.desc().nulls_last(), Lead.created_at.desc()),
            LeadSort.ACTIVITY: (last_message.desc().nulls_last(), Lead.created_at.desc()),
        }[filters.sort]
        statement = (
            base.add_columns(Contact, User, last_message, breached)
            .outerjoin(User, User.id == Lead.owner_id)
            .order_by(*order, Lead.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            LeadRow(lead, contact, owner, last_at, bool(was_breached))
            for lead, contact, owner, last_at, was_breached in rows
        ], int(total or 0)

    async def row(self, lead_id: uuid.UUID) -> LeadRow | None:
        statement = (
            select(Lead, Contact, User, _last_inbound(), _sla_breached())
            .join(Contact, Contact.id == Lead.contact_id)
            .outerjoin(User, User.id == Lead.owner_id)
            .where(Lead.id == lead_id)
        )
        found = (await self._session.execute(statement)).one_or_none()
        if found is None:
            return None
        lead, contact, owner, last_at, was_breached = found
        return LeadRow(lead, contact, owner, last_at, bool(was_breached))

    async def stats(self, *, since_24h: datetime, since_30d: datetime) -> dict[str, int]:
        open_statuses = [s for s in LeadStatus if s not in CLOSED_STATUSES]
        not_spam = or_(Lead.category.is_(None), Lead.category != LeadCategory.SPAM)
        row = (
            await self._session.execute(
                select(
                    func.count().filter(Lead.created_at >= since_24h, not_spam),
                    func.count().filter(
                        Lead.priority.in_([Priority.HIGH, Priority.CRITICAL]),
                        Lead.status.in_(open_statuses),
                    ),
                    func.count().filter(
                        _sla_breached(),
                        Lead.status == LeadStatus.NEW,
                        Lead.first_response_at.is_(None),
                    ),
                    func.count().filter(
                        Lead.status == LeadStatus.CONVERTED, Lead.converted_at >= since_30d
                    ),
                    func.count().filter(
                        or_(
                            and_(
                                Lead.status == LeadStatus.CONVERTED,
                                Lead.converted_at >= since_30d,
                            ),
                            and_(Lead.status == LeadStatus.LOST, _lost_since(since_30d)),
                        )
                    ),
                )
            )
        ).one()
        keys = (
            "new_24h",
            "high_priority_open",
            "unanswered_past_sla",
            "converted_30d",
            "closed_30d",
        )
        return {key: int(value or 0) for key, value in zip(keys, row, strict=True)}

    async def messages(self, lead_id: uuid.UUID) -> Sequence[Message]:
        result = await self._session.scalars(
            select(Message).where(Message.lead_id == lead_id).order_by(Message.occurred_at)
        )
        return result.all()

    async def events(self, lead_id: uuid.UUID) -> Sequence[LeadEvent]:
        result = await self._session.scalars(
            select(LeadEvent)
            .where(LeadEvent.lead_id == lead_id)
            .order_by(LeadEvent.created_at, LeadEvent.id)
        )
        return result.all()

    async def latest_score(self, lead_id: uuid.UUID) -> LeadScore | None:
        score: LeadScore | None = await self._session.scalar(
            select(LeadScore)
            .where(LeadScore.lead_id == lead_id)
            .order_by(LeadScore.created_at.desc(), LeadScore.id.desc())
            .limit(1)
        )
        return score

    async def leads_with_contacts(
        self, lead_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[Lead, Contact]]:
        if not lead_ids:
            return {}
        rows = await self._session.execute(
            select(Lead, Contact)
            .join(Contact, Contact.id == Lead.contact_id)
            .where(Lead.id.in_(lead_ids))
        )
        return {lead.id: (lead, contact) for lead, contact in rows.all()}
