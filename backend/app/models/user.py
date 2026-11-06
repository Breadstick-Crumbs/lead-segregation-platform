import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    str_enum,
    tz_datetime,
)
from app.models.enums import Role


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(str_enum(Role, "role"))
    # Null for accounts that only sign in with Microsoft.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # Entra object ID, linked on first Microsoft sign-in; stable even if the email changes.
    entra_oid: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(tz_datetime())

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def microsoft_linked(self) -> bool:
        return self.entra_oid is not None


class RefreshToken(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One row per issued refresh token. Rotation keeps a `family_id` per login so that
    reuse of an already-rotated token revokes the whole session (token theft detection)."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 hex, never the token
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    expires_at: Mapped[datetime] = mapped_column(tz_datetime())
    revoked_at: Mapped[datetime | None] = mapped_column(tz_datetime())
