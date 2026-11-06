"""Escalations to team chat through incoming webhooks: Slack, and Microsoft Teams Workflows.

An alert repeated after a retry is acceptable (it's an internal message), so any failure that
might pass on a second try is retryable."""

from typing import Any, Protocol

import httpx

from app.integrations.smtp import SendError
from app.schemas.alerts import Alert

HTTP_TIMEOUT_SECONDS = 10.0
SUMMARY_CHARS = 500


class ChatNotifier(Protocol):
    name: str

    async def send(self, alert: Alert) -> None: ...


def slack_escape(text: str) -> str:
    """Slack treats <...> as links and mentions (<!channel>), so lead text is escaped."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slack_text(text: str) -> dict[str, Any]:
    # verbatim: Slack must not turn names like "www.evil.test" or "#general" into links.
    return {"type": "mrkdwn", "text": text, "verbatim": True}


def slack_payload(alert: Alert) -> dict[str, Any]:
    title = slack_escape(alert.title)
    fields = [
        _slack_text(f"*{slack_escape(label)}*\n{slack_escape(value)}")
        for label, value in alert.facts
    ]
    return {
        "text": title,  # push-notification fallback
        "mrkdwn": False,  # ...shown as plain text
        "blocks": [
            {"type": "section", "text": _slack_text(f"*<{alert.url}|{title}>*")},
            {"type": "section", "fields": fields[:10]},  # Slack allows 10 fields
            {
                "type": "section",
                "text": _slack_text(slack_escape(alert.summary[:SUMMARY_CHARS]) or "-"),
            },
        ],
    }


def _runs(*runs: tuple[str, bool]) -> dict[str, Any]:
    """A RichTextBlock: unlike TextBlock and FactSet, its TextRuns don't parse Markdown, so
    lead text like "[Verify your login](https://evil.test)" stays text, not a link."""
    return {
        "type": "RichTextBlock",
        "inlines": [
            {"type": "TextRun", "text": text, **({"weight": "Bolder"} if bold else {})}
            for text, bold in runs
        ],
    }


def teams_payload(alert: Alert) -> dict[str, Any]:
    """An Adaptive Card, the format Teams Workflows webhooks accept (Office 365 connectors are
    retired). Every lead-derived string is a plain TextRun."""
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            _runs((alert.title, True)),
            *(_runs((f"{label}: ", True), (value, False)) for label, value in alert.facts),
            _runs((alert.summary[:SUMMARY_CHARS] or "-", False)),
        ],
        "actions": [{"type": "Action.OpenUrl", "title": "Open lead", "url": alert.url}],
    }
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }


class _WebhookNotifier:
    name = "webhook"

    def __init__(self, url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = url
        self._transport = transport

    def payload(self, alert: Alert) -> dict[str, Any]:
        raise NotImplementedError

    async def send(self, alert: Alert) -> None:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=HTTP_TIMEOUT_SECONDS
            ) as http:
                response = await http.post(self._url, json=self.payload(alert))
        except httpx.HTTPError as exc:
            # The exception text can include the URL, which is the credential: name only.
            raise SendError(
                f"{self.name} webhook unreachable: {type(exc).__name__}", retryable=True
            ) from exc
        if response.is_success:
            return
        retryable = (
            response.is_server_error or response.status_code == httpx.codes.TOO_MANY_REQUESTS
        )
        # Never log the URL: it is the credential.
        raise SendError(f"{self.name} webhook answered {response.status_code}", retryable=retryable)


class SlackNotifier(_WebhookNotifier):
    name = "slack"

    def payload(self, alert: Alert) -> dict[str, Any]:
        return slack_payload(alert)


class TeamsNotifier(_WebhookNotifier):
    name = "teams"

    def payload(self, alert: Alert) -> dict[str, Any]:
        return teams_payload(alert)
