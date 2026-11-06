import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    str_enum,
    tz_datetime,
)
from app.models.enums import (
    Channel,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    Priority,
    ProcessingState,
)

# Fixed by the column type; the embedding model is asked for exactly this many dimensions.
EMBEDDING_DIMENSIONS = 1536


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One inquiry from a contact. Follow-up messages in the same conversation attach to it."""

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        Index("ix_leads_status_created_at", "status", "created_at"),
        Index("ix_leads_created_at", "created_at"),  # reporting periods
        # Approximate nearest-neighbour search for similar leads (cosine distance).
        Index(
            "ix_leads_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), index=True
    )
    source: Mapped[Channel] = mapped_column(str_enum(Channel, "source"))
    category: Mapped[LeadCategory | None] = mapped_column(
        str_enum(LeadCategory, "category"), index=True
    )
    score: Mapped[int | None] = mapped_column(SmallInteger, index=True)
    priority: Mapped[Priority | None] = mapped_column(str_enum(Priority, "priority"))
    status: Mapped[LeadStatus] = mapped_column(
        str_enum(LeadStatus, "status"), default=LeadStatus.NEW, server_default=LeadStatus.NEW.value
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    language: Mapped[str | None] = mapped_column(String(8))  # ISO 639-1
    summary: Mapped[str | None] = mapped_column(Text)
    processing_state: Mapped[ProcessingState] = mapped_column(
        str_enum(ProcessingState, "processing_state"),
        default=ProcessingState.PENDING,
        server_default=ProcessingState.PENDING.value,
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    first_response_at: Mapped[datetime | None] = mapped_column(tz_datetime())
    converted_at: Mapped[datetime | None] = mapped_column(tz_datetime())
    # Embedding of the conversation text: similar leads, duplicates across addresses.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Near-identical text from a different contact shortly before (one sender, two addresses).
    possible_duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL")
    )


class LeadScore(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Every scoring run, kept for audit; the latest one is mirrored on `leads.score`."""

    __tablename__ = "lead_scores"
    __table_args__ = (CheckConstraint("total BETWEEN 0 AND 100", name="total_range"),)

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    total: Mapped[int] = mapped_column(SmallInteger)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rationale: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32))


class LeadEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only audit trail: drives response history and conversion reporting."""

    __tablename__ = "lead_events"
    __table_args__ = (Index("ix_lead_events_lead_id_created_at", "lead_id", "created_at"),)

    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    type: Mapped[LeadEventType] = mapped_column(str_enum(LeadEventType, "type"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
