"""Alerts about high-priority leads (docs/NOTIFICATIONS.md).

| Kind | When | In-app | Email | Slack / Teams |
|------|------|--------|-------|---------------|
| high_priority_lead | scored high | owner (admins if unassigned) | owner | - |
| escalation | scored critical | owner + admins | owner + escalation list | yes |
| sla_breach | unanswered past the SLA | owner + admins | owner + escalation list | yes |

Each kind is raised **once per lead**: under the lead's row lock, the alert's lead event, its
in-app notifications and the jobs for its external deliveries commit together. Deliveries
then run (and retry) on their own; a repeated chat or email alert after a retry is
acceptable, a missing one is not.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import SecretBox, SecretsNotConfiguredError
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.integrations.chat_webhooks import ChatNotifier
from app.integrations.smtp import EmailSender, SendError, SmtpCredentials, compose_alert
from app.models import Contact, Lead, Mailbox, Notification, User
from app.models.enums import (
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MailboxAuthType,
    NotificationKind,
    Priority,
)
from app.repositories.leads import CLOSED_STATUSES, LeadRepository
from app.repositories.mailboxes import MailboxRepository
from app.repositories.notifications import NotificationRepository
from app.schemas.alerts import Alert
from app.schemas.app_settings import NotificationSettings, ScoringThresholds
from app.services.app_settings import load_settings
from app.workers import names
from app.workers.queue import enqueue

logger = get_logger(__name__)

ESCALATING = frozenset({NotificationKind.ESCALATION, NotificationKind.SLA_BREACH})
SLA_SWEEP_BATCH = 200
EMAIL_CHANNEL = "email"
# A scored lead's own alert job normally runs within seconds; after this, raise it again.
MISSING_ALERT_GRACE = timedelta(minutes=15)


def alert_for_priority(priority: Priority | None) -> NotificationKind | None:
    if priority == Priority.CRITICAL:
        return NotificationKind.ESCALATION
    if priority == Priority.HIGH:
        return NotificationKind.HIGH_PRIORITY_LEAD
    return None


async def enqueue_alert(session: AsyncSession, lead_id: uuid.UUID, kind: NotificationKind) -> bool:
    return await enqueue(
        session,
        names.NOTIFY_LEAD,
        queueing_lock=f"notify:{lead_id}:{kind}",
        lead_id=str(lead_id),
        kind=kind.value,
    )


def build_alert(
    lead: Lead,
    contact: Contact | None,
    owner: User | None,
    kind: NotificationKind,
    *,
    frontend_url: str,
    sla_minutes: int,
) -> Alert:
    who = (
        (contact.company_name or contact.name or contact.email or contact.phone_e164)
        if contact
        else None
    ) or "Unknown sender"
    label = {
        NotificationKind.HIGH_PRIORITY_LEAD: "High-priority lead",
        NotificationKind.ESCALATION: "Critical lead",
        NotificationKind.SLA_BREACH: f"No response in {sla_minutes} min",
    }.get(kind, "Lead")
    category = (lead.category or "unclassified").replace("_", " ")
    return Alert(
        kind=kind,
        title=f"{label}: {who} (score {lead.score})"[:200],
        summary=lead.summary or "",
        facts=(
            ("Score", f"{lead.score} ({lead.priority})"),
            ("Category", category),
            ("Source", str(lead.source)),
            ("Owner", owner.full_name if owner else "Unassigned"),
        ),
        url=f"{frontend_url.rstrip('/')}/leads/{lead.id}",
    )


class NotificationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._leads = LeadRepository(session)
        self._notifications = NotificationRepository(session)

    # Raising alerts -------------------------------------------------------------------

    async def raise_alert(self, lead_id: uuid.UUID, kind: NotificationKind) -> bool:
        """Create the alert's in-app notifications and queue its deliveries, once. Commits.
        Returns False when there is nothing to do (already raised, or no longer relevant)."""
        lead = await self._leads.get(lead_id, for_update=True)
        if (
            lead is None
            or lead.status in CLOSED_STATUSES
            or lead.category in (None, LeadCategory.SPAM)
            or not _still_applies(lead, kind)
            or await self._notifications.alerted(lead_id, kind)
        ):
            await self._session.rollback()
            return False

        owner = await self._session.get(User, lead.owner_id) if lead.owner_id else None
        if owner is not None and not owner.is_active:
            owner = None
        admins = await self._notifications.active_admins()
        recipients = _in_app_recipients(kind, owner, admins)
        alert = await self._alert(lead, owner, kind)
        for user in recipients:
            await self._notifications.add(
                Notification(
                    user_id=user.id,
                    lead_id=lead.id,
                    kind=kind,
                    title=alert.title,
                    body=alert.summary,
                )
            )

        deliveries = await self._external_deliveries(kind, owner)
        for channel, target in deliveries:
            await enqueue(
                self._session,
                names.DELIVER_ALERT,
                lead_id=str(lead.id),
                kind=kind.value,
                channel=channel,
                target=target,
            )
        self._leads.add_event(
            lead.id,
            LeadEventType.ESCALATED if kind in ESCALATING else LeadEventType.NOTIFIED,
            {
                "kind": kind.value,
                "score": lead.score,
                "users": [str(u.id) for u in recipients],
                "channels": [channel for channel, _ in deliveries],
            },
        )
        await self._session.commit()
        logger.info(
            "alert_raised",
            lead_id=str(lead_id),
            kind=kind,
            users=len(recipients),
            deliveries=len(deliveries),
        )
        return True

    async def _external_deliveries(
        self, kind: NotificationKind, owner: User | None
    ) -> list[tuple[str, str | None]]:
        prefs = await load_settings(self._session, NotificationSettings)
        emails: list[str] = []
        if prefs.email_alerts:
            if owner is not None:
                emails.append(owner.email)
            if kind in ESCALATING:
                emails.extend(prefs.escalation_emails)
        deliveries: list[tuple[str, str | None]] = [
            (EMAIL_CHANNEL, email)
            for email in dict.fromkeys(emails)  # de-duplicated, in order
        ]
        if kind in ESCALATING:
            if self._settings.slack_webhook_url is not None:
                deliveries.append(("slack", None))
            if self._settings.teams_webhook_url is not None:
                deliveries.append(("teams", None))
        return deliveries

    async def _alert(self, lead: Lead, owner: User | None, kind: NotificationKind) -> Alert:
        contact = await self._session.get(Contact, lead.contact_id)
        thresholds = await load_settings(self._session, ScoringThresholds)
        return build_alert(
            lead,
            contact,
            owner,
            kind,
            frontend_url=self._settings.frontend_url,
            sla_minutes=thresholds.sla_minutes,
        )

    # Delivering ---------------------------------------------------------------------

    async def deliver(
        self,
        lead_id: uuid.UUID,
        kind: NotificationKind,
        channel: str,
        target: str | None,
        *,
        email: EmailSender,
        chat: dict[str, ChatNotifier],
        final_attempt: bool,
    ) -> bool:
        """Send one external alert. Raises a retryable SendError to be retried; returns
        False when it was given up (logged, and recorded on the lead)."""
        lead = await self._leads.get(lead_id)
        if lead is None:
            return False
        owner = await self._session.get(User, lead.owner_id) if lead.owner_id else None
        alert = await self._alert(lead, owner, kind)
        try:
            if channel == EMAIL_CHANNEL:
                if target is None:
                    raise SendError("No recipient", retryable=False)
                message, credentials = await self._alert_email(alert, target)
                await email.send(message, credentials)
            else:
                notifier = chat.get(channel)
                if notifier is None:
                    raise SendError(f"{channel} is not configured", retryable=False)
                await notifier.send(alert)
        except Exception as exc:
            retryable = exc.retryable if isinstance(exc, SendError) else True
            if retryable and not final_attempt:
                raise
            if not isinstance(exc, SendError):
                logger.exception("alert_delivery_crashed", lead_id=str(lead_id), channel=channel)
                exc = SendError(type(exc).__name__, retryable=False)
            await self._session.rollback()
            self._leads.add_event(
                lead_id,
                LeadEventType.NOTIFIED,
                {
                    "kind": f"{kind.value}_delivery_failed",
                    "channel": channel,
                    "error": str(exc)[:300],
                },
            )
            await self._session.commit()
            logger.error(
                "alert_delivery_failed", lead_id=str(lead_id), channel=channel, error=str(exc)
            )
            return False
        await self._session.rollback()  # read-only so far; end the transaction
        logger.info("alert_delivered", lead_id=str(lead_id), kind=kind, channel=channel)
        return True

    async def _alert_email(self, alert: Alert, to: str) -> tuple[EmailMessage, SmtpCredentials]:
        prefs = await load_settings(self._session, NotificationSettings)
        mailbox = await self._alert_mailbox(prefs.alert_mailbox_id)
        if mailbox is None:
            raise SendError("No active password mailbox to send alerts from", retryable=False)
        try:
            password = SecretBox(self._settings).decrypt(mailbox.encrypted_secret)
        except SecretsNotConfiguredError as exc:
            raise SendError(str(exc), retryable=False) from exc
        message = compose_alert(
            from_name=mailbox.name, from_address=mailbox.email_address, to_address=to, alert=alert
        )
        return message, SmtpCredentials(
            host=mailbox.smtp_host,
            port=mailbox.smtp_port,
            username=mailbox.username,
            password=password,
        )

    async def _alert_mailbox(self, mailbox_id: uuid.UUID | None) -> Mailbox | None:
        if mailbox_id is None:
            return await MailboxRepository(self._session).first_sending()
        chosen = await self._session.get(Mailbox, mailbox_id)
        if chosen and chosen.is_active and chosen.auth_type == MailboxAuthType.PASSWORD:
            return chosen
        logger.warning("alert_mailbox_unusable", mailbox_id=str(mailbox_id))
        return await MailboxRepository(self._session).first_sending()

    # The user's inbox ----------------------------------------------------------------

    async def page(
        self, user_id: uuid.UUID, *, unread_only: bool, limit: int, before: datetime | None
    ) -> tuple[Sequence[Notification], int]:
        items = await self._notifications.list_for(
            user_id, unread_only=unread_only, limit=limit, before=before
        )
        return items, await self._notifications.unread_count(user_id)

    async def mark_read(self, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
        await self._notifications.mark_read(user_id, datetime.now(UTC), notification_id)
        await self._session.commit()
        notification = await self._notifications.get_for(user_id, notification_id)
        if notification is None:  # someone else's, or gone: indistinguishable on purpose
            raise NotFoundError("Notification not found")
        return notification

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        updated = await self._notifications.mark_read(user_id, datetime.now(UTC))
        await self._session.commit()
        return updated

    # SLA ----------------------------------------------------------------------------

    async def sweep_sla(self) -> int:
        """Queue an SLA-breach escalation for each unanswered high or critical lead whose
        first alert is older than the SLA. Commits; returns how many were queued."""
        thresholds = await load_settings(self._session, ScoringThresholds)
        now = datetime.now(UTC)
        queued = 0
        for lead_id in await self._notifications.sla_breaches(
            prioritised_before=now - timedelta(minutes=thresholds.sla_minutes),
            limit=SLA_SWEEP_BATCH,
        ):
            queued += await enqueue_alert(self._session, lead_id, NotificationKind.SLA_BREACH)
        # An alert whose job gave up is raised again (still once: raise_alert checks).
        for lead_id, priority in await self._notifications.missing_alerts(
            scored_before=now - MISSING_ALERT_GRACE, limit=SLA_SWEEP_BATCH
        ):
            kind = alert_for_priority(priority)
            if kind is not None:
                queued += await enqueue_alert(self._session, lead_id, kind)
        await self._session.commit()
        return queued


def _in_app_recipients(
    kind: NotificationKind, owner: User | None, admins: Sequence[User]
) -> list[User]:
    if kind in ESCALATING:
        people = [owner, *admins] if owner else list(admins)
    else:
        people = [owner] if owner else list(admins)
    unique: dict[uuid.UUID, User] = {}
    for person in people:
        if person is not None:
            unique.setdefault(person.id, person)
    return list(unique.values())


def _still_applies(lead: Lead, kind: NotificationKind) -> bool:
    """Re-checked under the lock: a follow-up may have re-scored the lead since the job was
    queued, or someone may have answered it."""
    if kind == NotificationKind.SLA_BREACH:
        return lead.priority in (Priority.HIGH, Priority.CRITICAL) and not _answered(lead)
    if kind in (NotificationKind.HIGH_PRIORITY_LEAD, NotificationKind.ESCALATION):
        return alert_for_priority(lead.priority) == kind
    return True


def _answered(lead: Lead) -> bool:
    """A person has responded, or moved the lead on: the SLA is met."""
    return lead.first_response_at is not None or lead.status != LeadStatus.NEW
