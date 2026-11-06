import uuid
from datetime import UTC, datetime
from email.utils import format_datetime

import pytest

from app.integrations.email_parser import html_to_text, parse_email
from app.integrations.openai_audio import audio_filename
from app.schemas.inbound import InboundMessage
from app.services.ingestion import company_domain
from tests.fakes.messages import raw_email

MAILBOX = uuid.uuid4()
OWN = "sales@tawasol.test"


def _parse(raw: bytes) -> InboundMessage | None:
    return parse_email(raw, mailbox_id=MAILBOX, mailbox_address=OWN)


def _lead(raw: bytes) -> InboundMessage:
    message = _parse(raw)
    assert message is not None
    return message


def test_plain_email_is_parsed() -> None:
    message = _lead(raw_email())

    assert message.sender_address == "omar.haddad@gulfbuild.ae"
    assert message.sender_name == "Omar Haddad"
    assert message.subject == "Quotation for 40 seats"
    assert message.external_id == "<msg-1@gulfbuild.ae>"
    assert "pricing for 40 seats" in message.body_text
    assert message.mailbox_id == MAILBOX
    assert message.suppress_auto_reply is False


def test_html_only_email_becomes_readable_text() -> None:
    html = (
        "<html><head><style>p{}</style></head>"
        "<body><p>Hello</p><p>Need a <b>demo</b></p></body></html>"
    )

    message = _lead(raw_email(html=html))

    assert message.body_text == "Hello\nNeed a demo"


def test_thread_references_are_newest_first_without_duplicates() -> None:
    message = _lead(raw_email(in_reply_to="<c@x>", references="<a@x> <b@x> <c@x>"))

    assert message.thread_refs == ("<c@x>", "<b@x>", "<a@x>")


def test_missing_message_id_gets_an_id_that_survives_a_uidvalidity_reset() -> None:
    raw = raw_email(message_id=None)

    first, again = _lead(raw), _lead(raw)

    assert first.external_id == again.external_id
    assert first.external_id.startswith("<synthetic.")
    other_mailbox = parse_email(raw, mailbox_id=uuid.uuid4(), mailbox_address=OWN)
    assert other_mailbox is not None
    assert other_mailbox.external_id != first.external_id


@pytest.mark.parametrize(
    "raw",
    [
        raw_email(headers={"Auto-Submitted": "auto-replied"}),  # out of office
        raw_email(headers={"X-Autoreply": "yes"}),
        raw_email(headers={"Precedence": "auto_reply"}),
        raw_email(sender="Mail Delivery System <MAILER-DAEMON@gulfbuild.ae>"),
        raw_email(sender="Sales <sales@tawasol.test>"),  # our own mailbox
        raw_email(sender="undisclosed-recipients:;"),
    ],
)
def test_robots_answering_us_are_never_ingested(raw: bytes) -> None:
    assert _parse(raw) is None


def test_a_contact_form_lead_belongs_to_the_customer_in_reply_to() -> None:
    message = _lead(
        raw_email(
            sender="Website <wordpress@gulfbuild.ae>",
            headers={
                "Reply-To": "Layla Nasser <layla@customer.ae>",
                "Auto-Submitted": "auto-generated",
            },
            body="Name: Layla\nMessage: We want a demo for 12 users.",
        )
    )

    assert message.sender_address == "layla@customer.ae"
    assert message.sender_name == "Layla Nasser"
    assert message.suppress_auto_reply is False  # replying reaches the customer


@pytest.mark.parametrize(
    "raw",
    [
        raw_email(sender="no-reply@notifications.example.com"),
        raw_email(headers={"Precedence": "bulk"}),
        raw_email(headers={"List-Unsubscribe": "<mailto:u@list.example>"}),
        raw_email(headers={"X-Auto-Response-Suppress": "All"}),
        raw_email(headers={"Auto-Submitted": "auto-generated"}),
    ],
)
def test_mail_we_may_store_but_must_not_answer_is_flagged(raw: bytes) -> None:
    assert _lead(raw).suppress_auto_reply is True


def test_hostile_headers_are_cleaned_and_fitted() -> None:
    message = _lead(
        raw_email(
            sender=f'"{"A" * 300}" <x@attacker.test>',
            subject="Hi\x00there",
            body="Body with \x00 NUL and \x07 bell",
        )
    )

    assert message.sender_name is not None
    assert len(message.sender_name) == 200
    assert "\x00" not in (message.subject or "")
    assert "\x00" not in message.body_text
    assert "\x07" not in message.body_text


def test_a_future_date_is_clamped_to_now() -> None:
    future = format_datetime(datetime(2099, 1, 1, tzinfo=UTC))
    raw = (
        raw_email()
        .replace(b"Date: ", b"X-Old-Date: ", 1)
        .replace(b"Subject:", f"Date: {future}\nSubject:".encode(), 1)
    )

    assert _lead(raw).occurred_at <= datetime.now(UTC)


def test_auto_submitted_no_is_a_normal_email() -> None:
    message = _lead(raw_email(headers={"Auto-Submitted": "no"}))

    assert message.suppress_auto_reply is False


def test_arabic_email_survives_decoding() -> None:
    message = _lead(raw_email(subject="طلب عرض سعر", body="نحتاج عرض سعر لـ 40 مستخدم"))

    assert message.subject == "طلب عرض سعر"
    assert "عرض سعر" in message.body_text


def test_html_to_text_handles_entities() -> None:
    assert html_to_text("<p>Fish &amp; chips</p>") == "Fish & chips"


@pytest.mark.parametrize(
    ("email", "domain"),
    [("omar@gulfbuild.ae", "gulfbuild.ae"), ("someone@gmail.com", None), ("x@Outlook.com", None)],
)
def test_company_domain_ignores_free_webmail(email: str, domain: str | None) -> None:
    assert company_domain(email) == domain


@pytest.mark.parametrize(
    ("mime", "name"),
    [
        ("audio/ogg; codecs=opus", "voice-note.ogg"),
        ("audio/mpeg", "voice-note.mp3"),
        ("weird", "voice-note.ogg"),
    ],
)
def test_audio_filename_follows_the_mime_type(mime: str, name: str) -> None:
    assert audio_filename(mime) == name
