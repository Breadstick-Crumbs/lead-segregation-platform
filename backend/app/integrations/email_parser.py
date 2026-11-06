"""Parse raw RFC 5322 email into an InboundMessage.

Two kinds of automated mail are handled differently:
- **Never ingested:** bounces, out-of-office and other auto-replies (RFC 3834), and copies of
  our own mail. These are robots answering *us*, never leads.
- **Ingested but never auto-replied to:** bulk and mailing-list mail, mail asking not to be
  auto-answered, and mail from robot senders with no human Reply-To. A website contact form
  (`From: noreply@site`, `Reply-To: customer`) is a real lead, and the customer is the
  contact.
"""

import hashlib
import re
import uuid
from datetime import UTC, datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import ClassVar

from app.core.logging import get_logger
from app.core.text import clean
from app.models.enums import Channel
from app.schemas.inbound import InboundMessage

logger = get_logger(__name__)

MAX_BODY_CHARS = 20_000
MAX_ADDRESS_CHARS = 320
_MSGID = re.compile(r"<[^<>\s]+>")
_BOUNCE_LOCALPARTS = ("mailer-daemon", "postmaster")
_ROBOT_LOCALPARTS = ("noreply", "no-reply", "donotreply", "do-not-reply", "notifications")


class _TextExtractor(HTMLParser):
    _BLOCK: ClassVar[frozenset[str]] = frozenset(
        {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table", "section"}
    )

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in {"script", "style", "head"}:
            self._skip += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", joined)).strip()


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.text()


def _header(message: EmailMessage, name: str) -> str:
    return str(message.get(name, "")).strip().lower()


def is_auto_reply_or_bounce(message: EmailMessage, sender: str) -> bool:
    """A robot answering us (out-of-office, vacation, delivery failure): never a lead."""
    return (
        _header(message, "Auto-Submitted").startswith("auto-replied")
        or _header(message, "Precedence") == "auto_reply"
        or bool(message.get("X-Autoreply") or message.get("X-Autorespond"))
        or message.get_content_type() == "multipart/report"
        or sender.partition("@")[0] in _BOUNCE_LOCALPARTS
    )


def _single_address(message: EmailMessage, name: str) -> Address | None:
    header = message.get(name)
    addresses: tuple[Address, ...] = getattr(header, "addresses", ()) if header else ()
    if len(addresses) != 1:
        return None
    address = addresses[0]
    spec = address.addr_spec.lower()
    if "@" not in spec or len(spec) > MAX_ADDRESS_CHARS:
        return None
    return address


def _message_ids(value: object) -> tuple[str, ...]:
    return tuple(_MSGID.findall(str(value))) if value else ()


def _body(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):  # unknown charset or broken encoding
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    text = html_to_text(content) if part.get_content_type() == "text/html" else str(content)
    return clean(text.strip(), MAX_BODY_CHARS) or ""


def parse_email(
    raw: bytes, *, mailbox_id: uuid.UUID, mailbox_address: str
) -> InboundMessage | None:
    """None when the mail must not be ingested at all (see module docstring)."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(message, EmailMessage)  # noqa: S101  (guaranteed by policy.default)

    author = _single_address(message, "From")
    if author is None:
        return None
    author_spec = author.addr_spec.lower()
    own = mailbox_address.lower()
    if author_spec == own or is_auto_reply_or_bounce(message, author_spec):
        return None

    # Contact forms send as a robot with the customer in Reply-To: the customer is the lead.
    reply_to = _single_address(message, "Reply-To")
    contact = reply_to if reply_to is not None and reply_to.addr_spec.lower() != own else author
    sender = contact.addr_spec.lower()

    in_reply_to = _message_ids(message.get("In-Reply-To"))
    references = _message_ids(message.get("References"))
    # Newest first: In-Reply-To, then References from the most recent back.
    thread_refs = tuple(dict.fromkeys(in_reply_to + tuple(reversed(references))))
    message_ids = _message_ids(message.get("Message-ID"))

    return InboundMessage(
        channel=Channel.EMAIL,
        external_id=message_ids[0] if message_ids else _synthetic_id(mailbox_id, message),
        sender_address=sender,
        sender_name=clean(contact.display_name, 200) or None,
        occurred_at=_date(message),
        subject=clean(str(message.get("Subject", "")).strip(), 998) or None,
        body_text=_body(message),
        thread_refs=thread_refs,
        mailbox_id=mailbox_id,
        suppress_auto_reply=_suppress_auto_reply(
            message, sender, has_reply_to=reply_to is not None
        ),
    )


def _suppress_auto_reply(message: EmailMessage, sender: str, *, has_reply_to: bool) -> bool:
    """Ingest, but answering would reach a robot, a list, or someone who asked us not to."""
    auto_generated = _header(message, "Auto-Submitted") not in {"", "no"}
    return (
        _header(message, "Precedence") in {"bulk", "junk", "list"}
        or bool(message.get("List-Id") or message.get("List-Unsubscribe"))
        or bool(message.get("X-Auto-Response-Suppress"))
        or (auto_generated and not has_reply_to)
        or sender.partition("@")[0] in _ROBOT_LOCALPARTS
    )


def _synthetic_id(mailbox_id: uuid.UUID, message: EmailMessage) -> str:
    """For mail without a Message-ID. Built only from content that survives a mailbox's
    UIDVALIDITY reset, so a re-read message still dedupes."""
    digest = hashlib.sha256(
        "|".join(
            [
                str(mailbox_id),
                *(str(message.get(h, "")) for h in ("From", "To", "Date", "Subject")),
                _body(message),
            ]
        ).encode()
    ).hexdigest()[:32]
    return f"<synthetic.{digest}@lead-platform>"


def _date(message: EmailMessage) -> datetime:
    """The sender's Date header, but never in the future: a forged date must not reorder the
    conversation or the dashboard."""
    now = datetime.now(UTC)
    try:
        parsed = parsedate_to_datetime(str(message.get("Date")))
    except (TypeError, ValueError):
        return now
    parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return min(parsed, now)
