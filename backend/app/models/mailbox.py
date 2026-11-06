from datetime import datetime

from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum, tz_datetime
from app.models.enums import MailboxAuthType


class Mailbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A company inbox polled over IMAP and replied from over SMTP."""

    __tablename__ = "mailboxes"

    name: Mapped[str] = mapped_column(String(100))
    email_address: Mapped[str] = mapped_column(String(320), unique=True)
    imap_host: Mapped[str] = mapped_column(String(253))
    imap_port: Mapped[int] = mapped_column(default=993, server_default="993")
    smtp_host: Mapped[str] = mapped_column(String(253))
    smtp_port: Mapped[int] = mapped_column(default=587, server_default="587")
    username: Mapped[str] = mapped_column(String(320))
    auth_type: Mapped[MailboxAuthType] = mapped_column(str_enum(MailboxAuthType, "auth_type"))
    encrypted_secret: Mapped[str] = mapped_column(Text)  # Fernet: password or OAuth refresh token
    folder: Mapped[str] = mapped_column(String(200), default="INBOX", server_default="INBOX")
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger)
    last_uid: Mapped[int | None] = mapped_column(BigInteger)
    last_polled_at: Mapped[datetime | None] = mapped_column(tz_datetime())
    last_error: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
