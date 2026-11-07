from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import init_sentry


class CapturingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            event = item.get_event()
            if event is not None:
                self.events.append(dict(event))


@pytest.fixture
def sentry_events() -> Iterator[list[dict[str, Any]]]:
    settings = Settings(sentry_dsn="https://key@sentry.example.invalid/1", app_release="abc123")
    assert init_sentry(settings, "api")
    transport = CapturingTransport()
    sentry_sdk.get_client().transport = transport
    configure_logging("INFO", json_logs=True)
    yield transport.events
    sentry_sdk.get_global_scope().set_client(None)  # back to a client that sends nothing
    configure_logging("INFO", json_logs=False)


def test_without_a_dsn_sentry_stays_off() -> None:
    assert init_sentry(Settings(sentry_dsn=None), "api") is False
    assert not sentry_sdk.is_initialized()


def test_logged_exceptions_are_reported_with_their_fields(
    sentry_events: list[dict[str, Any]],
) -> None:
    try:
        raise RuntimeError("smtp exploded")
    except RuntimeError:
        get_logger("test").exception("outbound_crashed", lead_id="L1")
    get_logger("test").error("outbound_failed", lead_id="L2")
    get_logger("test").warning("just_a_warning")
    sentry_sdk.flush()

    crashed, failed = sentry_events
    assert crashed["exception"]["values"][-1]["value"] == "smtp exploded"
    assert crashed["extra"]["lead_id"] == "L1"
    assert crashed["release"] == "abc123"
    assert crashed["tags"]["component"] == "api"
    assert failed["message"] == "outbound_failed"
    assert failed["extra"]["lead_id"] == "L2"


def test_unhandled_request_errors_are_reported_without_bodies_or_credentials(
    sentry_events: list[dict[str, Any]],
) -> None:
    app = FastAPI()

    @app.post("/boom")
    async def boom(payload: dict[str, str]) -> None:  # parsed, so it is in frame locals
        raise ValueError("unexpected")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/boom",
            json={"password": "hunter2", "message": "lead text"},
            headers={"Authorization": "Bearer secret-token", "Cookie": "refresh_token=abc"},
        )
    sentry_sdk.flush()

    assert response.status_code == 500
    (event,) = [e for e in sentry_events if "exception" in e]
    assert event["exception"]["values"][-1]["value"] == "unexpected"
    request = event["request"]
    assert "data" not in request or not request["data"]
    for value in event["exception"]["values"]:  # source lines of this test aren't a leak
        for frame in value["stacktrace"]["frames"]:
            for key in ("pre_context", "context_line", "post_context"):
                frame.pop(key, None)
    flat = repr(event)
    for secret in ("hunter2", "lead text", "secret-token", "refresh_token=abc"):
        assert secret not in flat
