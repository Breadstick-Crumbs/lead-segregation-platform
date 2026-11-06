"""Data access for in-app notifications and the lead alert history."""

import json
import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead, LeadEvent, Notification, User
from app.models.enums import (
    LeadCategory,
    LeadEventType,
    LeadStatus,
    NotificationKind,
    Priority,
    Role,
)

# Postgres NOTIFY channel the API listens on to push notifications to open streams.
NOTIFY_CHANNEL = "user_notifications"
ALERT_EVENTS = (LeadEventType.NOTIFIED, LeadEventType.ESCALATED)
URGENT = (Priority.HIGH.value, Priority.CRITICAL.value)


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, notification: Notification) -> None:
        """Insert, and tell listening API processes once the transaction commits (NOTIFY is
        delivered on commit, never for a rolled-back insert)."""
        self._session.add(notification)
        await self._session.flush()
        payload = {
            "user_id": str(notification.user_id),
            "id": str(notification.id),
            "lead_id": str(notification.lead_id) if notification.lead_id else None,
            "kind": notification.kind,
            "title": notification.title,
        }
        await self._session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": NOTIFY_CHANNEL, "payload": json.dumps(payload)},
        )

    async def list_for(
        self, user_id: uuid.UUID, *, unread_only: bool, limit: int, before: datetime | None
    ) -> Sequence[Notification]:
        statement = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        if before is not None:
            statement = statement.where(Notification.created_at < before)
        result = await self._session.scalars(
            statement.order_by(Notification.created_at.desc(), Notification.id).limit(limit)
        )
        return result.all()

    async def unread_count(self, user_id: uuid.UUID) -> int:
        count = await self._session.scalar(
            select(func.count()).where(
                Notification.user_id == user_id, Notification.read_at.is_(None)
            )
        )
        return int(count or 0)

    async def mark_read(
        self, user_id: uuid.UUID, at: datetime, notification_id: uuid.UUID | None = None
    ) -> int:
        """Mark one (or, without an id, every) unread notification of this user as read."""
        statement = update(Notification).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
        if notification_id is not None:
            statement = statement.where(Notification.id == notification_id)
        result = await self._session.execute(
            statement.values(read_at=at).execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def get_for(self, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification | None:
        found: Notification | None = await self._session.scalar(
            select(Notification).where(
                Notification.id == notification_id, Notification.user_id == user_id
            )
        )
        return found

    async def alerted(self, lead_id: uuid.UUID, kind: NotificationKind) -> bool:
        found = await self._session.scalar(
            select(
                exists().where(
                    LeadEvent.lead_id == lead_id,
                    LeadEvent.type.in_(ALERT_EVENTS),
                    LeadEvent.payload["kind"].astext == kind.value,
                )
            )
        )
        return bool(found)

    async def missing_alerts(
        self, *, scored_before: datetime, limit: int
    ) -> Sequence[tuple[uuid.UUID, Priority]]:
        """Open high and critical leads with no alert for their priority yet, e.g. because
        the alert job ran out of retries."""
        kind_for = {
            Priority.HIGH: NotificationKind.HIGH_PRIORITY_LEAD,
            Priority.CRITICAL: NotificationKind.ESCALATION,
        }
        found: list[tuple[uuid.UUID, Priority]] = []
        for priority, kind in kind_for.items():
            alerted = exists().where(
                LeadEvent.lead_id == Lead.id,
                LeadEvent.type.in_(ALERT_EVENTS),
                LeadEvent.payload["kind"].astext == kind.value,
            )
            rows = await self._session.scalars(
                select(Lead.id)
                .where(
                    Lead.priority == priority,
                    Lead.status == LeadStatus.NEW,
                    Lead.category != LeadCategory.SPAM,
                    Lead.updated_at < scored_before,
                    ~alerted,
                )
                .order_by(Lead.updated_at)
                .limit(limit)
            )
            found.extend((lead_id, priority) for lead_id in rows)
        return found

    async def active_admins(self) -> Sequence[User]:
        result = await self._session.scalars(
            select(User).where(User.role == Role.ADMIN, User.is_active).order_by(User.created_at)
        )
        return result.all()

    async def sla_breaches(
        self, *, prioritised_before: datetime, limit: int
    ) -> Sequence[uuid.UUID]:
        """High and critical leads scored as such before `prioritised_before` that nobody has
        answered or moved on (still `new`, no first response), and not yet escalated for it.

        The clock starts when the lead was first scored high or critical, not when its alert
        went out, so a delayed or failed alert can't postpone or cancel the SLA."""
        became_urgent = exists().where(
            LeadEvent.lead_id == Lead.id,
            LeadEvent.type == LeadEventType.SCORED,
            LeadEvent.payload["priority"].astext.in_(URGENT),
            LeadEvent.created_at < prioritised_before,
        )
        breached = exists().where(
            LeadEvent.lead_id == Lead.id,
            LeadEvent.type == LeadEventType.ESCALATED,
            LeadEvent.payload["kind"].astext == NotificationKind.SLA_BREACH.value,
        )
        result = await self._session.scalars(
            select(Lead.id)
            .where(
                Lead.priority.in_([Priority.HIGH, Priority.CRITICAL]),
                Lead.status == LeadStatus.NEW,
                Lead.category != LeadCategory.SPAM,
                Lead.first_response_at.is_(None),
                became_urgent,
                ~breached,
            )
            .order_by(Lead.created_at)
            .limit(limit)
        )
        return result.all()
