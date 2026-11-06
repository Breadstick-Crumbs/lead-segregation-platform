import json

import httpx
import pytest

from app.integrations.whatsapp import WhatsAppClient, WhatsAppError, parse_statuses
from app.models.enums import DeliveryStatus
from tests.fakes.messages import PHONE_NUMBER_ID, wa_status_payload


def _client(handler: httpx.MockTransport) -> WhatsAppClient:
    return WhatsAppClient(access_token="t", graph_version="v26.0", transport=handler)


async def test_send_text_posts_a_threaded_text_and_returns_the_wamid() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT1"}]})

    wamid = await _client(httpx.MockTransport(handle)).send_text(
        phone_number_id="PN1", to="+971501234567", body="Thanks!", reply_to="wamid.IN1"
    )

    assert wamid == "wamid.OUT1"
    request = seen[0]
    assert request.url.path == "/v26.0/PN1/messages"
    sent = json.loads(request.content)
    assert sent["to"] == "971501234567"
    assert sent["text"]["body"] == "Thanks!"
    assert sent["context"] == {"message_id": "wamid.IN1"}


# Regression: a 5xx can come after Meta accepted the message, so retrying could send twice.
@pytest.mark.parametrize(("status", "retryable"), [(500, False), (429, True), (400, False)])
async def test_send_errors_from_meta(status: int, retryable: bool) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, json={"error": {"code": 131047, "message": "window"}})
    )

    with pytest.raises(WhatsAppError) as raised:
        await _client(transport).send_text(phone_number_id="PN1", to="+1", body="x")

    assert raised.value.retryable is retryable


async def test_a_connection_failure_is_retryable_but_a_read_timeout_is_not() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)  # Meta may have sent it

    with pytest.raises(WhatsAppError) as refused:
        await _client(httpx.MockTransport(refuse)).send_text(phone_number_id="P", to="+1", body="x")
    with pytest.raises(WhatsAppError) as timed_out:
        await _client(httpx.MockTransport(slow)).send_text(phone_number_id="P", to="+1", body="x")

    assert refused.value.retryable
    assert not timed_out.value.retryable


def test_delivery_statuses_are_parsed() -> None:
    payload = wa_status_payload(
        ("wamid.OUT1", "delivered", None),
        ("wamid.OUT2", "failed", {"code": 131047, "title": "Re-engagement message"}),
        ("wamid.OUT3", "deleted", None),  # unknown status: ignored
    )

    updates = parse_statuses(payload, phone_number_id=PHONE_NUMBER_ID)

    assert [(u.wamid, u.status) for u in updates] == [
        ("wamid.OUT1", DeliveryStatus.DELIVERED),
        ("wamid.OUT2", DeliveryStatus.FAILED),
    ]
    assert updates[1].error == "131047 Re-engagement message"
