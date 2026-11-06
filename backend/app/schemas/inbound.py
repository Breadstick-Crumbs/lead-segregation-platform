"""The channel-neutral shape every inbound message is normalised to before ingestion."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.models.enums import Channel, MessageKind


@dataclass(frozen=True)
class InboundMessage:
    channel: Channel
    external_id: str  # email Message-ID (or a synthesized id) / WhatsApp wamid
    sender_address: str  # lower-cased email address or E.164 phone number
    occurred_at: datetime
    body_text: str
    sender_name: str | None = None
    subject: str | None = None
    kind: MessageKind = MessageKind.TEXT
    media_id: str | None = None
    media_mime_type: str | None = None
    # Ids this message replies to (In-Reply-To/References, WhatsApp context id), newest first.
    thread_refs: tuple[str, ...] = field(default_factory=tuple)
    mailbox_id: uuid.UUID | None = None
    # Store it, but never auto-reply (bulk/list mail, robot senders, explicit opt-out).
    suppress_auto_reply: bool = False
