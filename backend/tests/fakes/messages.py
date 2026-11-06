"""Builders for realistic inbound payloads."""

from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

PHONE_NUMBER_ID = "109876543210"


def whatsapp_payload(*messages: dict[str, Any], name: str = "Fatima Al Mansoori") -> dict[str, Any]:
    wa_ids = {m["from"] for m in messages}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001111",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "contacts": [{"profile": {"name": name}, "wa_id": w} for w in wa_ids],
                            "messages": list(messages),
                        },
                    }
                ],
            }
        ],
    }


def wa_text(
    body: str,
    *,
    wamid: str = "wamid.A1",
    sender: str = "971501234567",
    ts: int | None = None,
    context_id: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "from": sender,
        "id": wamid,
        "timestamp": str(ts or int(datetime.now(UTC).timestamp())),
        "type": "text",
        "text": {"body": body},
    }
    if context_id:
        message["context"] = {"from": "15550001111", "id": context_id}
    return message


def wa_voice(
    *, wamid: str = "wamid.V1", sender: str = "971501234567", media_id: str = "MEDIA1"
) -> dict[str, Any]:
    return {
        "from": sender,
        "id": wamid,
        "timestamp": str(int(datetime.now(UTC).timestamp())),
        "type": "audio",
        "audio": {"id": media_id, "mime_type": "audio/ogg; codecs=opus", "voice": True},
    }


def raw_email(
    *,
    sender: str = "Omar Haddad <omar.haddad@gulfbuild.ae>",
    to: str = "sales@tawasol.test",
    subject: str = "Quotation for 40 seats",
    body: str = "Hi, we need pricing for 40 seats of your platform this month.",
    message_id: str | None = "<msg-1@gulfbuild.ae>",
    in_reply_to: str | None = None,
    references: str | None = None,
    html: str | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime(2026, 11, 6, 9, 30, tzinfo=UTC))
    if message_id:
        msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    for key, value in (headers or {}).items():
        msg[key] = value
    if html is not None:
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()
