from email.message import EmailMessage

import aiosmtplib
import pytest

from app.integrations.smtp import (
    AioSmtpSender,
    SendError,
    SmtpCredentials,
    compose_reply,
    new_message_id,
    reply_subject,
)
from app.schemas.app_settings import AutoReplyTemplates
from app.services.auto_reply import render_reply, thread_references

CREDENTIALS = SmtpCredentials(host="smtp.example.com", port=587, username="u", password="p")


def _reply(**overrides: object) -> EmailMessage:
    values: dict[str, object] = {
        "from_name": "Sales inbox",
        "from_address": "sales@example.com",
        "to_address": "omar@gulfbuild.ae",
        "subject": "Re: Quote",
        "body": "Thank you",
        "message_id": "<reply-1@example.com>",
        "in_reply_to": "<msg-2@gulfbuild.ae>",
        "references": ("<msg-1@gulfbuild.ae>", "<msg-2@gulfbuild.ae>"),
    }
    values.update(overrides)
    return compose_reply(**values)  # type: ignore[arg-type]


def test_reply_threads_under_the_customers_message() -> None:
    message = _reply()

    assert message["In-Reply-To"] == "<msg-2@gulfbuild.ae>"
    assert message["References"] == "<msg-1@gulfbuild.ae> <msg-2@gulfbuild.ae>"
    assert message["Message-ID"] == "<reply-1@example.com>"
    assert message["From"] == "Sales inbox <sales@example.com>"
    assert message.get_content().strip() == "Thank you"


def test_reply_is_marked_automatic_so_robots_do_not_answer() -> None:
    message = _reply()

    assert message["Auto-Submitted"] == "auto-replied"
    assert message["X-Auto-Response-Suppress"] == "All"


def test_a_reply_without_a_usable_message_id_has_no_thread_headers() -> None:
    message = _reply(in_reply_to=None, references=())

    assert message["In-Reply-To"] is None
    assert message["References"] is None


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("Quote", "Re: Quote"),
        ("RE: Quote", "RE: Quote"),
        ("re: quote", "re: quote"),
        (None, "Re:"),
        ("  ", "Re:"),
        ("hi\r\nBcc: v@evil.test", "Re: hi Bcc: v@evil.test"),  # decoded CR/LF
    ],
)
def test_reply_subject(original: str | None, expected: str) -> None:
    assert reply_subject(original, "Re: ") == expected


def test_message_ids_use_the_mailbox_domain() -> None:
    assert new_message_id("sales@tawasol.ae").endswith("@tawasol.ae>")


def test_templates_follow_the_lead_language_and_fill_the_name() -> None:
    templates = AutoReplyTemplates(
        templates={"en": "Hello {name}, thanks!", "ar": "مرحبا {name}، شكرا"}
    )

    assert render_reply(templates, language="en", name="Omar Haddad") == "Hello Omar, thanks!"
    assert render_reply(templates, language="ar", name=None) == "مرحبا، شكرا"
    assert render_reply(templates, language="fr", name=None) == "Hello, thanks!"


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (aiosmtplib.SMTPConnectError("refused"), True),
        (aiosmtplib.SMTPConnectTimeoutError("slow"), True),
        (aiosmtplib.SMTPResponseException(451, "try later"), True),
        (aiosmtplib.SMTPResponseException(550, "no such user"), False),
        (aiosmtplib.SMTPAuthenticationError(535, "bad credentials"), False),
        (aiosmtplib.SMTPRecipientsRefused([]), False),
        # Cut off mid-conversation: the message may have been accepted, so never resend.
        (aiosmtplib.SMTPServerDisconnected("gone"), False),
        (aiosmtplib.SMTPReadTimeoutError("timeout"), False),
    ],
)
async def test_smtp_failures_retry_only_when_certainly_unsent(
    monkeypatch: pytest.MonkeyPatch, error: Exception, retryable: bool
) -> None:
    async def fail(*_: object, **__: object) -> None:
        raise error

    monkeypatch.setattr(aiosmtplib, "send", fail)

    with pytest.raises(SendError) as raised:
        await AioSmtpSender().send(_reply(), CREDENTIALS)

    assert raised.value.retryable is retryable


@pytest.mark.parametrize(("port", "implicit", "starttls"), [(465, True, False), (587, False, True)])
async def test_smtp_always_uses_tls(
    monkeypatch: pytest.MonkeyPatch, port: int, implicit: bool, starttls: bool
) -> None:
    seen: dict[str, object] = {}

    async def capture(_: object, **kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(aiosmtplib, "send", capture)

    await AioSmtpSender().send(
        _reply(), SmtpCredentials(host="h", port=port, username="u", password="p")
    )

    assert (seen["use_tls"], seen["start_tls"]) == (implicit, starttls)


def test_references_are_oldest_first_without_duplicates() -> None:
    # Regression: ingestion stores refs newest first (In-Reply-To, then References reversed),
    # and the reply copied that order.
    stored = "<c@x> <c@x> <b@x> <a@x> <synthetic.1@lead-platform>"

    assert thread_references(stored, "<d@x>") == ("<a@x>", "<b@x>", "<c@x>", "<d@x>")
    assert thread_references(None, "<d@x>") == ("<d@x>",)
    assert thread_references("<a@x>", None) == ("<a@x>",)
