import hashlib
import hmac
import json

import pytest

from app.integrations.whatsapp import parse_webhook, verify_signature
from app.models.enums import Channel, MessageKind
from tests.fakes.messages import PHONE_NUMBER_ID, wa_text, wa_voice, whatsapp_payload

SECRET = "app-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted() -> None:
    body = json.dumps(whatsapp_payload(wa_text("hi"))).encode()

    assert verify_signature(body, _sign(body), SECRET)


@pytest.mark.parametrize("header", [None, "", "sha1=abc", "sha256=deadbeef"])
def test_missing_or_wrong_signature_is_rejected(header: str | None) -> None:
    assert not verify_signature(b"{}", header, SECRET)


def test_signature_covers_the_exact_bytes() -> None:
    body = b'{"a": 1}'

    assert not verify_signature(b'{"a":1}', _sign(body), SECRET)


def test_text_message_is_parsed() -> None:
    [message] = parse_webhook(
        whatsapp_payload(wa_text("Need a demo for our Dubai office", ts=1_790_000_000)),
        phone_number_id=PHONE_NUMBER_ID,
    )

    assert message.channel == Channel.WHATSAPP
    assert message.external_id == "wamid.A1"
    assert message.sender_address == "+971501234567"
    assert message.sender_name == "Fatima Al Mansoori"
    assert message.body_text == "Need a demo for our Dubai office"
    assert message.kind == MessageKind.TEXT
    assert message.occurred_at.timestamp() == 1_790_000_000


def test_voice_note_carries_the_media_reference_not_text() -> None:
    [message] = parse_webhook(whatsapp_payload(wa_voice()), phone_number_id=PHONE_NUMBER_ID)

    assert message.kind == MessageKind.VOICE
    assert message.media_id == "MEDIA1"
    assert message.media_mime_type == "audio/ogg; codecs=opus"
    assert message.body_text == ""


def test_image_keeps_its_caption_and_replies_keep_their_context() -> None:
    image = {
        "from": "971501234567",
        "id": "wamid.I1",
        "timestamp": "1790000000",
        "type": "image",
        "image": {"id": "IMG1", "mime_type": "image/jpeg", "caption": "Our floor plan"},
        "context": {"id": "wamid.OUR_REPLY"},
    }

    [message] = parse_webhook(whatsapp_payload(image), phone_number_id=PHONE_NUMBER_ID)

    assert (message.kind, message.body_text) == (MessageKind.IMAGE, "Our floor plan")
    assert message.thread_refs == ("wamid.OUR_REPLY",)


def test_interactive_reply_uses_the_chosen_title() -> None:
    reply = {
        "from": "971501234567",
        "id": "wamid.B1",
        "timestamp": "1790000000",
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": "x", "title": "Book a demo"},
        },
    }

    [message] = parse_webhook(whatsapp_payload(reply), phone_number_id=PHONE_NUMBER_ID)

    assert message.body_text == "Book a demo"


def test_statuses_reactions_and_other_numbers_are_ignored() -> None:
    reaction = {"from": "971501234567", "id": "wamid.R", "timestamp": "1", "type": "reaction"}
    payload = whatsapp_payload(reaction)
    payload["entry"][0]["changes"][0]["value"]["statuses"] = [{"id": "wamid.X", "status": "read"}]

    assert parse_webhook(payload, phone_number_id=PHONE_NUMBER_ID) == []
    assert parse_webhook(whatsapp_payload(wa_text("hi")), phone_number_id="another-number") == []


def test_malformed_payloads_do_not_raise() -> None:
    assert parse_webhook({}, phone_number_id=None) == []
    assert (
        parse_webhook({"entry": [{"changes": [{"field": "messages"}]}]}, phone_number_id=None) == []
    )


def test_a_non_ascii_signature_is_rejected_not_an_error() -> None:
    assert not verify_signature(b"{}", "sha256=\xe9\xe9", SECRET)


def test_one_malformed_message_does_not_drop_the_rest() -> None:
    broken = wa_text("bad", wamid="wamid.BAD")
    broken["timestamp"] = "not-a-number"
    payload = whatsapp_payload(broken, wa_text("good", wamid="wamid.GOOD"))

    parsed = parse_webhook(payload, phone_number_id=PHONE_NUMBER_ID)

    assert [m.external_id for m in parsed] == ["wamid.GOOD"]


def test_non_list_sections_are_tolerated() -> None:
    payload = {"entry": [{"changes": [{"field": "messages", "value": {"messages": "oops"}}]}]}

    assert parse_webhook(payload, phone_number_id=None) == []
