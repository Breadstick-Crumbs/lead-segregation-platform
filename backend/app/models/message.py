import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, str_enum, tz_datetime
from app.models.enums import (
    Channel,
    DeliveryStatus,
    MessageDirection,
    MessageKind,
    TranscriptionStatus,
)


class Message(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Idempotent ingestion: email Message-ID / WhatsApp wamid is unique per channel.
        UniqueConstraint("channel", "external_id", name="uq_messages_channel_external_id"),
        # NULLs never collide in a unique constraint, so inbound rows must carry an id (an email
        # without Message-ID gets a synthesized one) or re-polls would insert duplicates.
        CheckConstraint(
            "direction = 'outbound' OR external_id IS NOT NULL", name="inbound_has_external_id"
        ),
        # At most one auto-reply per lead, so a retried job can never send a second one. A failed
        # send is retried by updating this row's delivery_status, never by inserting another.
        Index(
            "uq_messages_one_auto_reply_per_lead",
            "lead_id",
            unique=True,
            postgresql_where=text("is_auto_reply"),
        ),
        Index("ix_messages_lead_id_occurred_at", "lead_id", "occurred_at"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mailboxes.id", ondelete="SET NULL")
    )
    channel: Mapped[Channel] = mapped_column(str_enum(Channel, "channel"))
    direction: Mapped[MessageDirection] = mapped_column(str_enum(MessageDirection, "direction"))
    external_id: Mapped[str | None] = mapped_column(String(512))
    thread_ref: Mapped[str | None] = mapped_column(Text)  # In-Reply-To/References or WA context
    sender_address: Mapped[str] = mapped_column(String(320))  # email address or E.164 number
    subject: Mapped[str | None] = mapped_column(String(998))  # RFC 5322 line limit
    body_text: Mapped[str] = mapped_column(Text)
    kind: Mapped[MessageKind] = mapped_column(
        str_enum(MessageKind, "kind"),
        default=MessageKind.TEXT,
        server_default=MessageKind.TEXT.value,
    )
    media_id: Mapped[str | None] = mapped_column(String(200))  # WhatsApp media id
    media_mime_type: Mapped[str | None] = mapped_column(String(100))
    transcription_status: Mapped[TranscriptionStatus | None] = mapped_column(
        str_enum(TranscriptionStatus, "transcription_status")
    )
    is_auto_reply: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Inbound mail we must not answer automatically (lists, robots, opt-outs). Phase 4 checks it.
    suppress_auto_reply: Mapped[bool] = mapped_column(default=False, server_default="false")
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        str_enum(DeliveryStatus, "delivery_status")
    )
    occurred_at: Mapped[datetime] = mapped_column(tz_datetime())  # received or sent time
    # Outbound only. Set just before handing the message to the provider and cleared if
    # the provider certainly didn't take it; a row still `queued` with this set means the
    # outcome is unknown, so it is never sent again (at most once).
    send_started_at: Mapped[datetime | None] = mapped_column(tz_datetime())
    delivery_error: Mapped[str | None] = mapped_column(Text)
