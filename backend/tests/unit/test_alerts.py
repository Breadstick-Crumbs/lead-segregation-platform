import json
import uuid

import httpx
import pytest

from app.integrations.chat_webhooks import (
    SlackNotifier,
    TeamsNotifier,
    slack_payload,
    teams_payload,
)
from app.integrations.smtp import SendError, compose_alert
from app.models.enums import NotificationKind, Role
from app.models.user import User
from app.schemas.alerts import Alert
from app.services.notifications import _in_app_recipients

ALERT = Alert(
    kind=NotificationKind.ESCALATION,
    title="Critical lead: Gulf <Build> & Co (score 88)",
    summary="Wants 200 seats <!channel> now",
    facts=(("Score", "88 (critical)"), ("Owner", "Rep One")),
    url="https://leads.example.test/leads/abc",
)


def test_slack_escapes_lead_text_so_it_cannot_mention_or_link() -> None:
    payload = json.dumps(slack_payload(ALERT))

    assert "<!channel>" not in payload
    assert "Gulf &lt;Build&gt; &amp; Co" in payload
    assert "<https://leads.example.test/leads/abc|" in payload


def test_teams_gets_an_adaptive_card_with_an_open_button() -> None:
    payload = teams_payload(ALERT)

    [attachment] = payload["attachments"]
    card = attachment["content"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["type"] == "AdaptiveCard"
    assert card["actions"][0]["url"] == ALERT.url
    texts = [run["text"] for block in card["body"] for run in block["inlines"]]
    assert texts[1:3] == ["Score: ", "88 (critical)"]


def test_teams_lead_text_is_never_markdown() -> None:
    # Regression: TextBlock and FactSet render Markdown, so a sender named
    # "[Verify your login](https://evil.test)" became a link in the managers' channel.
    phishing = Alert(
        kind=NotificationKind.ESCALATION,
        title="Critical lead: [Verify your login](https://evil.test)",
        summary="**urgent** [click](https://evil.test)",
        facts=(("Owner", "[x](https://evil.test)"),),
        url="https://leads.example.test/leads/1",
    )

    card = teams_payload(phishing)["attachments"][0]["content"]

    assert {block["type"] for block in card["body"]} == {"RichTextBlock"}
    assert all(run["type"] == "TextRun" for b in card["body"] for run in b["inlines"])


def test_every_slack_text_object_is_verbatim() -> None:
    # Regression: titles and fields were auto-linked ("www.evil.test", "#general").
    payload = slack_payload(ALERT)

    objects = [
        payload["blocks"][0]["text"],
        *payload["blocks"][1]["fields"],
        payload["blocks"][2]["text"],
    ]
    assert all(o["type"] == "mrkdwn" and o["verbatim"] is True for o in objects)
    assert payload["mrkdwn"] is False


@pytest.mark.parametrize("notifier_class", [SlackNotifier, TeamsNotifier])
@pytest.mark.parametrize(("status", "retryable"), [(500, True), (429, True), (404, False)])
async def test_webhook_failures(
    notifier_class: type[SlackNotifier] | type[TeamsNotifier], status: int, retryable: bool
) -> None:
    secret_url = "https://hooks.example.test/T000/B000/secret-token"
    notifier = notifier_class(
        secret_url, transport=httpx.MockTransport(lambda _: httpx.Response(status))
    )

    with pytest.raises(SendError) as raised:
        await notifier.send(ALERT)

    assert raised.value.retryable is retryable
    assert "secret-token" not in str(raised.value)  # the URL is the credential


async def test_webhook_success_posts_json() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    await SlackNotifier("https://hooks.example.test/x", transport=httpx.MockTransport(handle)).send(
        ALERT
    )

    assert json.loads(seen[0].content)["text"].startswith("Critical lead")


def test_alert_email_is_marked_automatic_and_links_the_lead() -> None:
    message = compose_alert(
        from_name="Sales", from_address="sales@example.com", to_address="boss@x.test", alert=ALERT
    )

    assert message["Auto-Submitted"] == "auto-generated"
    assert message["Subject"] == ALERT.title
    assert ALERT.url in message.get_content()


def _user(role: Role = Role.SALES_REP) -> User:
    return User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@x.test", full_name="U", role=role)


def test_recipients_per_kind() -> None:
    owner, admin = _user(), _user(Role.ADMIN)

    assert _in_app_recipients(NotificationKind.HIGH_PRIORITY_LEAD, owner, [admin]) == [owner]
    assert _in_app_recipients(NotificationKind.HIGH_PRIORITY_LEAD, None, [admin]) == [admin]
    assert _in_app_recipients(NotificationKind.ESCALATION, owner, [admin]) == [owner, admin]
    # An admin who owns the lead is told once.
    assert _in_app_recipients(NotificationKind.SLA_BREACH, admin, [admin]) == [admin]
