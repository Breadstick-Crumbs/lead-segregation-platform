import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from app.models.enums import MailboxAuthType
from app.schemas.user import NormalizedEmail

Host = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=253)]
Port = Annotated[int, Field(ge=1, le=65535)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class MailboxCreate(BaseModel):
    name: Name
    email_address: NormalizedEmail
    imap_host: Host
    imap_port: Port = 993
    smtp_host: Host
    smtp_port: Port = 587
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=320)]
    password: SecretStr = Field(min_length=1, max_length=500)
    folder: Annotated[str, StringConstraints(min_length=1, max_length=200)] = "INBOX"


class MailboxUpdate(BaseModel):
    name: Name | None = None
    imap_host: Host | None = None
    imap_port: Port | None = None
    smtp_host: Host | None = None
    smtp_port: Port | None = None
    username: str | None = Field(None, min_length=1, max_length=320)
    password: SecretStr | None = Field(None, min_length=1, max_length=500)
    folder: str | None = Field(None, min_length=1, max_length=200)
    is_active: bool | None = None


class MailboxRead(BaseModel):
    """Never includes the stored secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email_address: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    username: str
    auth_type: MailboxAuthType
    folder: str
    is_active: bool
    last_polled_at: datetime | None
    last_error: str | None
    created_at: datetime


class PollRequested(BaseModel):
    queued: bool = Field(description="False if a poll for this mailbox was already waiting")
