# Notifications and escalation

High and critical leads alert people, in the app and outside it. A lead that nobody answers
within the SLA is escalated again.

## Who is told what

| Alert | When | In-app | Email | Slack / Teams |
|-------|------|--------|-------|---------------|
| `high_priority_lead` | Scored high (60–79) | Owner, or every admin if unassigned | Owner | – |
| `escalation` | Scored critical (80+) | Owner and every admin | Owner and the escalation list | Yes |
| `sla_breach` | High or critical, still `new` with no first response, the SLA (60 min) after it was first scored high or critical | Owner and every admin | Owner and the escalation list | Yes |

Spam and closed leads are never alerted. A lead first scored high that is re-scored critical
after a follow-up gets the escalation too. Each kind is raised once per lead.

When an alert job runs, it checks the lead's current priority, so a lead re-scored lower
in the meantime isn't alerted. The SLA clock starts at scoring, not at the alert, so a
delayed or failed alert can't postpone the escalation. The sweep also raises any high or
critical alert that is still missing after 15 minutes, for example when its job gave up.

**What counts as answered.** A lead is answered once someone moves it out of `new` or
records a first response (`first_response_at`). The dashboard actions that do this
(change status, reply) arrive in Phase 6. Until then, every unanswered high or critical
lead escalates after the SLA. Replies sent from outside the app, such as Outlook or a
phone, aren't detected.

Settings, admin-editable at `app_settings` key `notifications`:

- `escalation_emails`: sales managers, who don't need accounts;
- `email_alerts`: on or off;
- `alert_mailbox_id`: the mailbox alerts are sent from. If it isn't set, the first active
  mailbox is used.

The SLA is `scoring.thresholds.sla_minutes`. Chat webhooks are secrets in `.env`:

- `SLACK_WEBHOOK_URL`: a Slack incoming webhook;
- `TEAMS_WEBHOOK_URL`: a Teams *Workflows* webhook. Office 365 connectors are retired.

## How it works

```
lead_pipeline (scored high/critical) ──▶ notify_lead(kind)          same transaction as the score
sweep_sla (every minute) ─────────────▶ notify_lead(sla_breach)

notify_lead  lock the lead row; already raised this kind? → stop
             insert in-app notifications + pg_notify per user
             lead event (notified / escalated) with recipients and channels
             queue deliver_alert per email recipient, Slack, Teams          one commit
deliver_alert  render from the lead now, send, retry 5 runs; record a failure on the lead
```

- **Once:** the lead event is the marker. It is checked and written under the lead's row
  lock, so concurrent runs, re-scores and the sweep can't raise a kind twice. In-app
  notifications and the queued deliveries commit with it, so either all of an alert exists
  or none of it does.
- **External delivery is at-least-once.** A retried Slack message or email may repeat; a
  lost one is worse for an alert. Any failure that might pass later is retried. Webhook
  URLs are never logged.
- **Untrusted text:** lead names and AI summaries can't format or link in any channel.
  - **Slack:** escaped, and every text object is `verbatim`, so there are no mentions,
    auto-links or `<url|text>`.
  - **Teams:** `RichTextBlock` text runs, which don't parse Markdown the way `TextBlock`
    and `FactSet` do.
  - **Email:** plain text.
- **Email alerts** are sent from a password mailbox. If the chosen mailbox can't send, the
  first active one is used instead. OAuth (XOAUTH2) sending isn't built yet, so an
  OAuth-only setup records email alerts as failed. In-app and chat alerts still work.

## The live stream

Run `GET /api/v1/notifications/stream` with `Authorization: Bearer <access token>`. It
sends Server-Sent Events:

- `ready` when the stream opens;
- `notification`, one per new notification: `{id, user_id, lead_id, kind, title}`;
- `resync` after the API's listener reconnects (for example, a database restart): events
  may have been missed, so reload the list;
- `expired` when the access token runs out. Refresh the token and reconnect.

EventSource can't send headers, so the dashboard uses a fetch-based SSE client.

Each API process keeps one `LISTEN user_notifications` connection. The worker's insert
calls `pg_notify` in the same transaction, so a stream hears only about committed
notifications, from any process. The listener reconnects with backoff. Streams don't hold
database connections: the user is checked once, then the session closes.

NOTIFY isn't stored, so a disconnected stream misses events. On (re)connect the dashboard
reloads `GET /api/v1/notifications`, which returns the newest first, the unread count, and
`before` for paging. The stream only makes it live.

The other endpoints are `POST /api/v1/notifications/{id}/read` and
`POST /api/v1/notifications/read-all`. Users only ever see and change their own
notifications; someone else's id answers 404.

Behind Caddy, the API route needs `flush_interval -1`, so events aren't buffered (see
`docs/DEPLOYMENT.md`).
