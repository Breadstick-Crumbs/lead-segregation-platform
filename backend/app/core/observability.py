"""Sentry error reporting. Off unless `SENTRY_DSN` is set: without it nothing is initialised
and nothing leaves the process."""

import sys
from collections.abc import MutableMapping
from typing import Any, Literal

import sentry_sdk

from app.core.config import Settings

Component = Literal["api", "worker"]


def init_sentry(settings: Settings, component: Component) -> bool:
    """Start reporting unhandled errors and error-level logs. Returns whether it is on."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.app_release,
        server_name=component,
        # Leads' messages, passwords, tokens and OAuth codes must never leave the process, so
        # collect nothing from requests beyond the denylist-scrubbed headers, and no stack
        # frame variables (a frame's locals can hold any of them).
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "denylist"}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "stack_frame_variables": False,
            "database_query_data": False,
            "gen_ai": {"inputs": False, "outputs": False},
        },
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    sentry_sdk.set_tag("component", component)
    return True


# Not sent as extra data: `error` and `detail` fields carry a provider's error text, which
# can quote a phone number or address back.
_NOT_EXTRAS = frozenset({"event", "exc_info", "error", "errors", "detail"})


def report_errors_to_sentry(
    _: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: send `logger.error(...)` and `logger.exception(...)` to Sentry.

    structlog doesn't log through the standard library, so Sentry's logging integration never
    sees these: they are the errors the app catches and handles (a job that crashed, a failed
    delivery), which are exactly the ones worth knowing about. The log's key-value fields
    become the event's extra data (IDs; never secrets or message bodies, and never the
    free-text `error` fields)."""
    # `Any` in the signature: structlog's processor protocol passes the wrapped logger.
    if method_name not in ("error", "exception", "critical") or not sentry_sdk.is_initialized():
        return event_dict
    exc_info = event_dict.get("exc_info")
    if exc_info is True:
        exc_info = sys.exc_info()
    extras = {k: v for k, v in event_dict.items() if k not in _NOT_EXTRAS}
    with sentry_sdk.new_scope() as scope:
        for key, value in extras.items():
            scope.set_extra(key, value)
        if isinstance(exc_info, BaseException):
            sentry_sdk.capture_exception(exc_info)
        elif isinstance(exc_info, tuple) and exc_info[1] is not None:
            sentry_sdk.capture_exception(exc_info[1])
        else:
            sentry_sdk.capture_message(str(event_dict.get("event")), level="error")
    return event_dict
