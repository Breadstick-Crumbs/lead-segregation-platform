"""What an alert about a lead says, independent of the channel that carries it."""

from dataclasses import dataclass

from app.models.enums import NotificationKind


@dataclass(frozen=True)
class Alert:
    kind: NotificationKind
    title: str  # one line, e.g. "Critical lead: Gulf Build LLC (score 86)"
    summary: str  # the AI summary; untrusted text, escape it per channel
    facts: tuple[tuple[str, str], ...]  # label/value pairs: score, category, source, owner
    url: str  # the lead in the dashboard
