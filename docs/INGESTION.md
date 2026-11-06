# Lead ingestion (WhatsApp and email)

## Flow

```
Meta webhook ──▶ POST /api/v1/webhooks/whatsapp
                 verify X-Hub-Signature-256 → store raw payload (inbound_webhooks)
                 → enqueue process_whatsapp_webhook   (same transaction) → 200
                                       │
IMAP (every minute) ──▶ poll_all_mailboxes → poll_mailbox (one per mailbox, locked)
                                       │
                                       ▼
                      IngestionService.ingest(InboundMessage)
                      upsert contact → per-contact advisory lock → dedupe
                      → find conversation or open lead → store message + events
                      → enqueue transcribe_message (voice) or process_lead (same transaction)
                                       │
voice notes ──▶ transcribe_message: Graph API media download → OpenAI speech-to-text
                                       │
                                       ▼
                      process_lead → AI pipeline (docs/AI_PIPELINE.md)
```

Jobs run in the **worker** process (Procrastinate, in Postgres). Because SQLAlchemy and
Procrastinate share the psycopg 3 connection, a job is inserted in the same transaction as
the rows it refers to. A crash can't leave a lead without its job, or a job without its lead.

## Guarantees

| Concern | How |
|---------|-----|
| Meta retries / IMAP re-reads | `UNIQUE (channel, external_id)`; ingestion is a no-op for a known message |
| Email without Message-ID | Synthesized id from mailbox, UIDVALIDITY and UID, plus a header hash |
| Two messages from one contact at once | Serialised per contact (upsert row lock plus `pg_advisory_xact_lock`): one lead, not two |
| Forged `In-Reply-To` | A reply only joins a lead of the same contact |
| Mail loops | Out-of-office/auto-replies (RFC 3834), bounces and our own mail are never ingested. Bulk and list mail, robot senders and `X-Auto-Response-Suppress` mail are stored with `suppress_auto_reply`, and Phase 4 never answers them |
| Contact forms | `Reply-To` (one address) is the contact, so a form sent as `noreply@site` becomes the customer's lead |
| Hostile input | Text is stripped of NUL/control characters and fitted to its column; an oversized Message-ID is hashed (also in thread refs); future `Date` headers are clamped to now. A message that still fails to store is skipped and named in `mailboxes.last_error` |
| Oversized webhook bodies | Refused with 413 above 3 MB, before buffering |
| Slow or broken IMAP | Per-message progress (`last_uid`); errors saved on `mailboxes.last_error`; a queueing lock stops polls piling up |
| UIDVALIDITY reset | Re-read by date window; Message-ID dedupe absorbs the overlap (synthetic ids are content-based, so they survive too) |
| Two workers, one mailbox | Polls share a Procrastinate `lock`, so they never run at once |
| Transcription outages | Retried with backoff. On the last attempt, or a permanent error (no key, media older than 7 days), the message is marked failed with a placeholder and the lead moves on |
| Parsing bugs | The raw payload is kept; one malformed message doesn't drop the rest; the job records the error and can be re-run |
| Growth | Nightly `prune_history`: succeeded jobs after 7 days, processed payloads after 30 days. Failures are kept |

## Conversations

- **Email:** a reply (`In-Reply-To`/`References` matching a stored message) continues that
  lead. Anything else from the same person is a new lead.
- **WhatsApp:** a reply to a specific message continues its lead. Otherwise the contact's
  open lead continues if they wrote within the last `whatsapp_conversation_hours` (default
  24).
- Converted, lost and spam leads are never reopened. A follow-up message puts an open lead
  back to `processing_state = pending` so the AI pipeline re-reads it.

## Hand-off to the AI pipeline

`process_lead` runs the pipeline described in `docs/AI_PIPELINE.md`. The Phase 2 hand-off
notes are handled there: a sweep re-queues stranded `pending` leads (skipping ones still
being transcribed), and results are written only if no newer message arrived meanwhile.

## Setting up WhatsApp (Meta test number)

1. In [Meta for Developers](https://developers.facebook.com/), create an app (type
   *Business*) and add the **WhatsApp** product. Meta provisions a test number.
2. Under **WhatsApp → API Setup**, note the **Phone number ID**, generate an **access token**
   (use a System User token for anything long-lived), and add up to 5 recipient numbers you
   can test from.
3. Under **App settings → Basic**, copy the **App secret**.
4. Expose the API publicly. In development:

   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```

5. Under **WhatsApp → Configuration → Webhook**:
   - **Callback URL:** `https://<tunnel>/api/v1/webhooks/whatsapp`
   - **Verify token:** any random string
   - Subscribe to the **messages** field.
6. Set these in `backend/.env`, then restart the API and the worker:

   ```
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_ACCESS_TOKEN=...
   WHATSAPP_APP_SECRET=...
   WHATSAPP_VERIFY_TOKEN=<the string from step 5>
   OPENAI_API_KEY=...            # for voice-note transcription
   ```

## Adding a mailbox

Set `SECRETS_ENCRYPTION_KEY`, then as an admin call `POST /api/v1/mailboxes` with the IMAP
and SMTP host, username and password (the dashboard gets a form in Phase 6). Passwords are
Fernet-encrypted and never returned. `POST /api/v1/mailboxes/{id}/poll` checks the inbox
immediately. A new mailbox imports only the last `initial_lookback_days` (default 1), not
the whole inbox.

- **Gmail / Google Workspace:** IMAP host `imap.gmail.com`, with an app password (requires
  2-Step Verification).
- **Microsoft 365:** IMAP basic auth is retired, so these mailboxes need OAuth2 (XOAUTH2).
  The model supports `auth_type = oauth2_xoauth2`, but the token flow isn't built yet; such
  mailboxes report an explicit error.
- **Other hosts** (cPanel, Zoho, and so on): the provider's IMAP host and the account
  password.
