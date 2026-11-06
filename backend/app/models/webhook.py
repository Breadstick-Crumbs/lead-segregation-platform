from datetime import datetime
from typing import Any

from sqlalchemy import Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, str_enum, tz_datetime
from app.models.enums import WebhookSource


class InboundWebhook(UUIDPrimaryKeyMixin, Base):
    """Raw webhook payloads, stored before any parsing.

    The webhook request only verifies the signature, stores the body and enqueues a job, so
    Meta gets its 200 at once, a parsing bug never loses a lead, and payloads can be replayed.
    """

    __tablename__ = "inbound_webhooks"
    __table_args__ = (
        Index(
            "ix_inbound_webhooks_unprocessed",
            "received_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    source: Mapped[WebhookSource] = mapped_column(str_enum(WebhookSource, "source"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(tz_datetime(), server_default=text("now()"))
    processed_at: Mapped[datetime | None] = mapped_column(tz_datetime())
    error: Mapped[str | None] = mapped_column(Text)
