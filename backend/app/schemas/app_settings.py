"""Admin-editable configuration, stored as JSONB in `app_settings` (one row per model).

The defaults are the documented behaviour (docs/SCORING.md); the seed script writes them and
admins change them later through the settings API."""

import uuid
from typing import ClassVar, Self

from pydantic import BaseModel, Field, model_validator

from app.schemas.user import NormalizedEmail


class SettingsModel(BaseModel):
    """One settings document; `KEY` is its row in `app_settings`."""

    KEY: ClassVar[str]


class ScoringWeights(SettingsModel):
    KEY: ClassVar[str] = "scoring.weights"

    intent: int = Field(25, ge=0, le=100)
    urgency: int = Field(15, ge=0, le=100)
    business_value: int = Field(20, ge=0, le=100)
    keywords: int = Field(15, ge=0, le=100)
    company: int = Field(15, ge=0, le=100)
    history: int = Field(10, ge=0, le=100)

    @model_validator(mode="after")
    def _not_all_zero(self) -> Self:
        if sum(self.model_dump().values()) == 0:
            raise ValueError("At least one scoring weight must be above zero")
        return self


class ScoringThresholds(SettingsModel):
    KEY: ClassVar[str] = "scoring.thresholds"

    auto_reply_above: int = Field(20, ge=0, le=100, description="Reply when score > this")
    high_at: int = Field(60, ge=0, le=100)
    critical_at: int = Field(80, ge=0, le=100, description="Escalate at or above this")
    support_score_cap: int = Field(40, ge=0, le=100)
    spam_score_cap: int = Field(5, ge=0, le=100)
    sla_minutes: int = Field(60, ge=1, le=7 * 24 * 60)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.auto_reply_above < self.high_at <= self.critical_at:
            raise ValueError("Thresholds must satisfy auto_reply_above < high_at <= critical_at")
        return self


class KeywordRule(BaseModel):
    term: str = Field(min_length=1, max_length=100)
    points: int = Field(ge=1, le=100)


class ScoringKeywords(SettingsModel):
    KEY: ClassVar[str] = "scoring.keywords"

    rules: list[KeywordRule] = Field(
        default_factory=lambda: [
            KeywordRule(term=term, points=points)
            for term, points in [
                ("pricing", 25),
                ("price", 20),
                ("quote", 25),
                ("quotation", 25),
                ("proposal", 20),
                ("rfp", 35),
                ("tender", 35),
                ("demo", 25),
                ("trial", 20),
                ("contract", 30),
                ("purchase", 30),
                ("budget", 25),
                ("urgent", 15),
                ("asap", 15),
                ("partnership", 20),
                ("enterprise", 20),
                ("عرض سعر", 25),  # quotation
                ("السعر", 20),  # the price
                ("عرض تجريبي", 25),  # demo
                ("مناقصة", 35),  # tender
                ("عاجل", 15),  # urgent
                ("شراكة", 20),  # partnership
            ]
        ]
    )


class AutoReplyTemplates(SettingsModel):
    KEY: ClassVar[str] = "auto_reply.templates"

    default_language: str = "en"
    subject_prefix: str = "Re: "
    templates: dict[str, str] = Field(
        default_factory=lambda: {
            "en": (
                "Thank you for contacting us. We have received your inquiry and a member of "
                "our team will get in touch with you shortly."
            ),
            "ar": ("شكرًا لتواصلك معنا. لقد استلمنا استفسارك وسيتواصل معك أحد أعضاء فريقنا قريبًا."),
        }
    )

    @model_validator(mode="after")
    def _has_default(self) -> Self:
        if self.default_language not in self.templates:
            raise ValueError("A template for the default language is required")
        return self


class IngestionSettings(SettingsModel):
    KEY: ClassVar[str] = "ingestion"

    # A WhatsApp message joins the contact's open lead if their last message was this recent;
    # otherwise it starts a new lead. (Email threads by In-Reply-To/References instead.)
    whatsapp_conversation_hours: int = Field(24, ge=1, le=24 * 30)
    # A newly added mailbox imports this far back, not the whole inbox.
    initial_lookback_days: int = Field(1, ge=0, le=90)


class SimilaritySettings(SettingsModel):
    """pgvector lookups. Similarity is cosine similarity (1 = identical text)."""

    KEY: ClassVar[str] = "ai.similarity"

    # Neighbours at least this similar count as "leads like this" for the history factor.
    similar_min: float = Field(0.75, ge=0, le=1)
    similar_limit: int = Field(10, ge=1, le=50)
    # A lead this similar to one from a *different* contact, within the window, is flagged as
    # a possible duplicate (the same person writing from two addresses).
    duplicate_min: float = Field(0.95, ge=0, le=1)
    duplicate_window_days: int = Field(7, ge=1, le=90)


class NotificationSettings(SettingsModel):
    """Who hears about high-priority leads, and how (docs/NOTIFICATIONS.md)."""

    KEY: ClassVar[str] = "notifications"

    # Emailed on critical leads and SLA breaches (sales managers; need not be users).
    escalation_emails: list[NormalizedEmail] = Field(default_factory=list, max_length=50)
    email_alerts: bool = True
    # The mailbox alerts are sent from; None means the first active mailbox.
    alert_mailbox_id: uuid.UUID | None = None


DEFAULT_SETTINGS: tuple[SettingsModel, ...] = (
    ScoringWeights(),
    ScoringThresholds(),
    ScoringKeywords(),
    AutoReplyTemplates(),
    IngestionSettings(),
    SimilaritySettings(),
    NotificationSettings(),
)
