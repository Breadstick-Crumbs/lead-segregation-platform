# Architecture

## Overview

```
                ┌──────────────┐      ┌────────────────────┐
 Company inbox ─┤ IMAP poller  │      │ Meta WhatsApp      │
 (any provider) │ (worker cron)│      │ Cloud API webhook  │
                └──────┬───────┘      └─────────┬──────────┘
                       │ normalized InboundMessage│
                       ▼                          ▼
                ┌───────────────────────────────────────────┐
                │ Ingestion service (idempotent, dedup by    │
                │ external message id) → contact + lead +    │
                │ message rows in Postgres                   │
                └──────────────────┬────────────────────────┘
                                   │ enqueue process_lead(lead_id)
                                   ▼
                ┌───────────────────────────────────────────┐
                │ Worker pipeline                            │
                │ 1. classify  (OpenAI structured output)    │
                │ 2. score     (LLM signals + rule signals)  │
                │ 3. assign    (owner assignment rules)      │
                │ 4. respond   (score > 20 and not spam →    │
                │               ack via same channel)        │
                │ 5. notify    (high priority / escalation)  │
                └──────────────────┬────────────────────────┘
                                   ▼
        ┌──────────────────────────────────────────────────────┐
        │ FastAPI REST API (/api/v1) + SSE stream               │
        └──────────────────┬───────────────────────────────────┘
                           ▼
        ┌──────────────────────────────────────────────────────┐
        │ React dashboard: leads, detail + history, reports,    │
        │ notifications, settings (admin)                       │
        └──────────────────────────────────────────────────────┘
```

Processes (one image, separate entrypoints):

| Process  | Responsibility |
|----------|----------------|
| `api`    | REST API, WhatsApp webhook, SSE notifications, auth |
| `worker` | Lead pipeline jobs, outbound sends, notifications, retries, and periodic jobs (IMAP polling, SLA/escalation sweeps) |
| `web`    | Static React build (nginx in prod, Vite in dev) |
| `postgres` | Storage, job queue (Procrastinate) and LISTEN/NOTIFY for SSE fan-out |

## Design principles

- **Ingest fast, process async.** Webhooks return 200 within milliseconds after
  persisting the raw message; all AI and outbound work happens in the worker.
  Meta retries webhooks that are slow or fail, so the handler must be idempotent.
- **Transactional enqueue.** Jobs live in Postgres (Procrastinate), so the
  lead row and its `process_lead` job commit in one transaction: no lost or
  phantom jobs, and no Redis to run.
- **Idempotency everywhere.** Inbound messages are unique on
  `(channel, external_id)` (email `Message-ID`, WhatsApp `wamid`). Auto-replies
  are recorded before/after send with a unique constraint per lead so retries
  never double-send.
- **Channels are adapters.** `ChannelAdapter` (ingest + send) and `Notifier`
  (in-app, email, Slack/Teams) are interfaces; adding SMS or Telegram later
  means adding one adapter, not touching the pipeline.
- **AI is a dependency, not the system.** The classifier sits behind an
  interface; if OpenAI fails or times out, the lead is still stored, marked
  `processing_failed`, retried with backoff, and visible on the dashboard.
- **Explainable scores.** Every score stores its per-factor breakdown and the
  model's rationale, so sales can see *why* a lead is 82.
- **Layering.** `api` (routers, HTTP only) → `services` (business logic) →
  `repositories` (DB access) → `models`. Integrations live in
  `integrations/` and are injected into services. No SQL in routers, no HTTP
  in services.

## Backend layout

```
backend/
  app/
    main.py                 # app factory, middleware, router registration
    core/                   # settings, security, logging, errors
    db/                     # engine, session, base model
    models/                 # SQLAlchemy ORM models
    schemas/                # Pydantic request/response models
    repositories/           # data access, one per aggregate
    services/               # ingestion, classification, scoring, assignment,
                            # responder, notifications, reporting
    integrations/           # imap, smtp, whatsapp, openai, slack/teams
    api/v1/                 # routers: auth, leads, users, reports,
                            # notifications (SSE), settings, webhooks
    workers/                # Procrastinate app, tasks, periodic jobs
  migrations/               # Alembic
  tests/                    # unit/ integration/ (mirrors app/)
```

## Frontend layout

```
frontend/
  src/
    app/          # router, providers, layout shell
    api/          # generated OpenAPI client + query hooks
    features/     # leads/, reports/, notifications/, settings/, auth/
    components/   # shared UI (shadcn/ui based)
    lib/          # utils, formatting
```

## Data model (core tables)

Defined in `backend/app/models/`; the initial migration is the source of truth.

| Table | Key columns and guarantees |
|-------|----------------------------|
| `users` | email (unique, CHECK lower-case), full_name, role (`admin`/`sales_rep`), password_hash (Argon2id), is_active, last_login_at. At least one active admin always remains (row-locked check). |
| `refresh_tokens` | user_id, token_hash (SHA-256, unique), family_id, expires_at, revoked_at. Claimed with one atomic `UPDATE … RETURNING`; reuse of a rotated token revokes the family. |
| `contacts` | name, email (unique, lower-case), phone_e164 (unique), company_name, company_domain. CHECK: email or phone present. |
| `leads` | contact_id, source, category, score (0–100), priority, status, owner_id, language, summary, processing_state, processing_error, first_response_at, converted_at |
| `messages` | lead_id, mailbox_id, channel, direction, external_id, thread_ref, sender_address, subject, body_text, is_auto_reply, sent_by_user_id, delivery_status, occurred_at. UNIQUE (channel, external_id); inbound rows must have an external_id (an email without Message-ID gets a synthesized one); at most one auto-reply per lead (partial unique index). |
| `lead_scores` | lead_id, total, breakdown (JSONB), rationale, model, prompt_version |
| `lead_events` | lead_id, type, actor_id, payload (JSONB). Append-only audit trail. |
| `notifications` | user_id, lead_id, kind, title, body, read_at (in-app only; email and Slack deliveries are lead events) |
| `mailboxes` | name, email_address, imap/smtp host and port, username, auth_type (`password`/`oauth2_xoauth2`), encrypted_secret (Fernet), folder, uidvalidity, last_uid, last_polled_at, last_error, is_active |
| `app_settings` | key, value (JSONB), updated_by_id. Validated by `app/schemas/app_settings.py`. |

`lead_events` powers response history and conversion tracking
(time-to-first-response, status funnel).

**Auto-reply sending:** insert the auto-reply message row first (`queued`), send it, then
update its `delivery_status`. A failed send is retried by updating that same row; inserting
a second one would violate the one-auto-reply index.

**Auth on the client:** refresh rotation treats a second use of a token as theft, so the
dashboard must refresh single-flight (one shared in-flight promise, coordinated across tabs).

## Lead lifecycle

`new → contacted → qualified → converted` with `lost` and `spam` as terminal
states. `processing_state`: `pending → processed | failed`, separate from the
sales status.
