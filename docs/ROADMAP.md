# Roadmap

Each phase ends with green checks (lint, types, tests, build) and a PR.

| # | Phase | Deliverable |
|---|-------|-------------|
| 0 | **Groundwork** | Repo, docs, backend and frontend scaffolds, config, logging, health endpoint, Docker Compose, pre-commit, CI |
| 1 | Data model and auth | SQLAlchemy models, Alembic migrations, seed script, JWT + refresh auth, RBAC (`admin`, `sales_rep`), user management |
| 2 | Ingestion | `InboundMessage` contract; WhatsApp webhook (verify + signature check + dedup); IMAP mailbox poller; contact/lead/message upsert; Procrastinate setup |
| 3 | AI pipeline | OpenAI classifier (structured output, versioned prompt), hybrid scorer, owner assignment, labelled fixture set and evaluation test |
| 4 | Auto-response | Channel senders (SMTP with threading, WhatsApp text); idempotent auto-reply for score > 20; per-language templates; delivery status |
| 5 | Notifications | `Notifier` interface: in-app (SSE), email, Slack/Teams; thresholds; SLA escalation sweep |
| 6 | Dashboard | Login, lead table (filters, sort, search), lead detail (messages, score breakdown, history, reassign, status change), notifications bell, settings screens |
| 7 | Reporting | Volume by source, category distribution, conversion funnel and rate, time to first response, high-value lead analytics |
| 8 | Hardening | E2E tests, rate limiting, security review, Sentry, prod Docker images, deployment, README with demo walkthrough |
