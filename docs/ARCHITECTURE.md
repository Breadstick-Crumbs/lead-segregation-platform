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

| Table | Key columns |
|-------|-------------|
| `users` | id, email, name, role (`admin`/`sales_rep`), password_hash, is_active |
| `contacts` | id, name, email, phone_e164, company_name, company_domain, first_seen_at |
| `leads` | id, contact_id, source (`email`/`whatsapp`), category, score, priority, status, owner_id, language, processing_state, created_at, first_response_at, converted_at |
| `messages` | id, lead_id, channel, direction (`inbound`/`outbound`), external_id (unique per channel), thread_ref, subject, body_text, sent_by (`system`/user id), delivery_status, created_at |
| `lead_scores` | id, lead_id, total, breakdown (JSONB), rationale, model, prompt_version, created_at |
| `lead_events` | id, lead_id, type (status_changed, assigned, auto_replied, escalated, note…), actor_id, payload (JSONB), created_at |
| `notifications` | id, user_id, lead_id, kind, channel, read_at, created_at |
| `mailboxes` | id, name, imap_host, smtp_host, username, auth_type (`password`/`oauth2_xoauth2`), encrypted_secret, uidvalidity, last_uid, is_active |
| `settings` | key, value (JSONB): thresholds, weights, keyword lists, auto-reply templates, assignment rules |

`lead_events` is the audit trail and powers response history and conversion
tracking (time-to-first-response, status funnel).

## Lead lifecycle

`new → contacted → qualified → converted` with `lost` and `spam` as terminal
states. `processing_state`: `pending → processed | failed`, separate from the
sales status.
