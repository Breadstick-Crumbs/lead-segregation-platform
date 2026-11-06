from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Contact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The person behind one or more leads, matched by email or WhatsApp number."""

    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("email IS NOT NULL OR phone_e164 IS NOT NULL", name="has_identifier"),
        # Contact matching depends on one canonical form per address.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20), unique=True)
    company_name: Mapped[str | None] = mapped_column(String(200))
    company_domain: Mapped[str | None] = mapped_column(String(253), index=True)
