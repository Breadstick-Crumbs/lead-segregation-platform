"""Domain enumerations, stored as their string values."""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    SALES_REP = "sales_rep"


class Channel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class LeadCategory(StrEnum):
    SALES_OPPORTUNITY = "sales_opportunity"
    DEMO_REQUEST = "demo_request"
    PARTNERSHIP_INQUIRY = "partnership_inquiry"
    SUPPORT_REQUEST = "support_request"
    GENERAL_INQUIRY = "general_inquiry"
    SPAM = "spam"


class LeadStatus(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    CONVERTED = "converted"
    LOST = "lost"
    SPAM = "spam"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProcessingState(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"  # WhatsApp voice note / audio; body_text holds the transcript
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    INTERACTIVE = "interactive"  # button or list reply


class TranscriptionStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class WebhookSource(StrEnum):
    WHATSAPP = "whatsapp"


class DeliveryStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class LeadEventType(StrEnum):
    CREATED = "created"
    MESSAGE_RECEIVED = "message_received"
    CLASSIFIED = "classified"
    SCORED = "scored"
    ASSIGNED = "assigned"
    STATUS_CHANGED = "status_changed"
    AUTO_REPLIED = "auto_replied"
    REPLIED = "replied"
    NOTIFIED = "notified"
    ESCALATED = "escalated"
    NOTE = "note"
    PROCESSING_FAILED = "processing_failed"


class NotificationKind(StrEnum):
    HIGH_PRIORITY_LEAD = "high_priority_lead"
    ESCALATION = "escalation"
    ASSIGNMENT = "assignment"
    SLA_BREACH = "sla_breach"


class MailboxAuthType(StrEnum):
    PASSWORD = "password"  # noqa: S105  (an auth method name, not a credential)
    OAUTH2_XOAUTH2 = "oauth2_xoauth2"
