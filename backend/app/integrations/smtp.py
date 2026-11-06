"""Outbound email over SMTP: composing a threaded reply and sending it.

Failures are sorted three ways, because a resend must never duplicate a reply:
- the server **certainly didn't take** the message (couldn't connect, 4xx before data was
  accepted): `SendError(retryable=True)`;
- it **refused** it (5xx, bad credentials): `SendError(retryable=False)`;
- the outcome is **unknown** (disconnect or timeout mid-conversation): also not retryable,
  since the message may have gone out.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Protocol

import aiosmtplib

SMTP_TIMEOUT_SECONDS = 30.0
IMPLICIT_TLS_PORT = 465
SMTP_TRANSIENT = range(400, 500)  # 4xx: try again later


class SendError(Exception):
    """Sending failed. `retryable` is True only when the message certainly wasn't sent."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class SmtpCredentials:
    host: str
    port: int
    username: str
    password: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage, credentials: SmtpCredentials) -> None: ...


def new_message_id(from_address: str) -> str:
    domain = from_address.rpartition("@")[2] or "lead-platform"
    return make_msgid(domain=domain)


def compose_reply(
    *,
    from_name: str,
    from_address: str,
    to_address: str,
    subject: str,
    body: str,
    message_id: str,
    in_reply_to: str | None,
    references: tuple[str, ...],
) -> EmailMessage:
    """A plain-text reply that threads under the customer's message (RFC 5322 §3.6.4) and
    is marked as automatic (RFC 3834), so other autoresponders don't answer it."""
    message = EmailMessage()
    message["From"] = Address(display_name=from_name, addr_spec=from_address)
    message["To"] = to_address
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now(UTC))
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = " ".join(references)
    message["Auto-Submitted"] = "auto-replied"
    message["X-Auto-Response-Suppress"] = "All"  # Exchange/Outlook out-of-office replies
    message.set_content(body)
    return message


def reply_subject(original: str | None, prefix: str) -> str:
    # One line: a decoded subject can carry CR/LF, which a header can't.
    subject = " ".join((original or "").split())
    if not subject:
        return prefix.strip() or "Re:"
    if subject.lower().startswith(prefix.strip().lower()):
        return subject
    return f"{prefix}{subject}"


class AioSmtpSender:
    async def send(self, message: EmailMessage, credentials: SmtpCredentials) -> None:
        implicit_tls = credentials.port == IMPLICIT_TLS_PORT
        try:
            await aiosmtplib.send(
                message,
                hostname=credentials.host,
                port=credentials.port,
                username=credentials.username,
                password=credentials.password,
                use_tls=implicit_tls,
                start_tls=not implicit_tls,  # never send credentials in clear text
                timeout=SMTP_TIMEOUT_SECONDS,
            )
        except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPConnectTimeoutError) as exc:
            raise SendError(f"Couldn't connect: {exc}", retryable=True) from exc
        except aiosmtplib.SMTPRecipientsRefused as exc:
            raise SendError("SMTP refused the recipient", retryable=False) from exc
        except aiosmtplib.SMTPAuthenticationError as exc:
            raise SendError(f"SMTP login failed: {exc.code}", retryable=False) from exc
        except aiosmtplib.SMTPResponseException as exc:
            # A refusal is an answer, so the message wasn't accepted; 4xx may pass later.
            raise SendError(
                f"SMTP refused: {exc.code} {exc.message}", retryable=exc.code in SMTP_TRANSIENT
            ) from exc
        except aiosmtplib.SMTPException as exc:  # disconnect or timeout: may have been sent
            raise SendError(f"SMTP outcome unknown: {exc!r}", retryable=False) from exc
