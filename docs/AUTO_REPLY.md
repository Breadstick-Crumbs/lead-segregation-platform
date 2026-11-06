# Auto-reply

When the AI pipeline scores a lead above the threshold (default: above 20) and it isn't
spam, the same transaction queues `send_auto_reply`. The job sends one acknowledgement
through the channel the lead came from.

## Who gets a reply

Each condition is checked again when the job runs:

- The score is above `auto_reply_above`, the category isn't spam, and the lead isn't
  converted, lost or spam.
- The latest customer message isn't flagged `suppress_auto_reply`. Mailing lists, bulk
  mail, `noreply` robots and `X-Auto-Response-Suppress` senders are stored as leads but
  never answered. Out-of-office replies and bounces never become leads at all.
- **WhatsApp:** the customer wrote within the last 24 hours. Meta allows free-form
  messages only inside that window; later contact needs approved templates, which aren't
  built.
- The lead has no auto-reply yet. A partial unique index allows exactly one per lead, so
  a re-score after a follow-up never sends a second.

## What is sent

- **Text:** the admin template for the lead's language (`auto_reply.templates`), falling
  back to the default language. `{name}` becomes the contact's first name.
- **Email:** sent from the mailbox the lead came in on, over SMTP with TLS (implicit on
  465, STARTTLS otherwise), as `Re: <subject>`.
  - `In-Reply-To` and `References` thread it under the customer's message, unless that
    message had no real Message-ID. `References` lists the customer's chain oldest first,
    then their message.
  - `Auto-Submitted: auto-replied` (RFC 3834) and `X-Auto-Response-Suppress: All` stop
    other autoresponders from answering it.
  - Its Message-ID is fixed before sending, so the customer's answer threads back onto
    the same lead.
- **WhatsApp:** a text message quoting the customer's message (`context`).

## At most once

1. The reply row is inserted as `queued` and committed.
2. The send is claimed atomically: `UPDATE … SET send_started_at = now() WHERE queued AND
   send_started_at IS NULL`. A run that loses the claim stops.
3. The provider is called. On success, the row becomes `sent`, with the provider's id and
   an `auto_replied` event.

Jobs for one lead share a Procrastinate `lock`, so two never run at once. A second job can
still be queued while the first runs, for example after a quick follow-up.

A failure is retried only when the provider certainly didn't take the message: it
couldn't connect, or it answered SMTP 4xx or HTTP 429. In that case `send_started_at` is
cleared first. A Graph API 5xx is not retried, because it can come after Meta accepted
the message.

A disconnect or timeout mid-conversation may mean the message went out. So do a refusal
(SMTP 5xx, bad credentials, Meta 4xx) and the last of 5 attempts. Each of these marks
the reply `failed`, with `delivery_error` and an event.

If a run dies mid-send, the row is left `queued` with `send_started_at` set. The next
run marks it `failed` ("outcome unknown") instead of sending again. When a
`send_auto_reply` job's worker stops sending heartbeats, the 5-minute sweep queues that
lead's job again, then fails the stalled one. The new job waits on the shared lock and
then reaches this decision.

## Delivery status

WhatsApp receipts (`sent`, `delivered`, `read`, `failed`) arrive on the same webhook and
update the reply by its wamid. Statuses only move forward, so a late `delivered` can't
undo `read`, and a `failed` after delivery is ignored. Moving forward clears an earlier
error. Meta can report on a message before our send has saved its wamid, so receipts
that match nothing are checked once more a minute later, then dropped. Email has no receipts: `sent`
means the SMTP server accepted it.

## Known trade-offs

- Contact forms: the reply goes to the `Reply-To` address, which the sender controls.
  The body is a fixed template, so it can't carry anyone's content. The worst case is
  one unwanted acknowledgement, and only for a lead that scored above the threshold.
- A retried send doesn't re-check eligibility. Retries finish within about 12
  minutes.

## Not built yet

- XOAUTH2 sending (Microsoft 365 or Google Workspace with OAuth). Such mailboxes record a
  clear failure.
- WhatsApp template messages outside the 24-hour window.
