# Roadmap

Each phase ends with green checks (lint, types, tests, build) and a PR.

| # | Phase | Deliverable | Status |
|---|-------|-------------|--------|
| 0 | **Groundwork** | Repo, docs, backend and frontend scaffolds, config, logging, health endpoint, Docker Compose, pre-commit, CI | Done |
| 1 | **Data model and auth** | SQLAlchemy models, Alembic migrations, seed script, JWT + rotating refresh auth, RBAC (`admin`, `sales_rep`), user management | Done (PR #1) |
| 1b | **Sign in with Microsoft** ★ | OIDC authorization-code flow with PKCE against Microsoft Entra ID (single tenant). Users are matched to admin-created accounts and linked by their Entra object ID. | In progress |
| 2 | Ingestion | `InboundMessage` contract; WhatsApp webhook (verify + signature check + dedup); IMAP mailbox poller; contact/lead/message upsert; Procrastinate setup. ★ **WhatsApp voice notes**: download the media and transcribe it with OpenAI speech-to-text (Arabic and English), so voice-only leads get scored like text. | In progress |
| 3 | AI pipeline | OpenAI classifier (structured output, versioned prompt), hybrid scorer, owner assignment, labelled fixture set and evaluation test. ★ **pgvector**: an embedding per lead powers "similar past leads" (feeds the previous-interactions score), cross-address duplicate detection, and "leads like this converted". | |
| 4 | Auto-response | Channel senders (SMTP with threading, WhatsApp text); idempotent auto-reply for score > 20; per-language templates; delivery status | |
| 5 | Notifications | `Notifier` interface: in-app (SSE), email, Slack/Teams; thresholds; SLA escalation sweep | |
| 6 | Dashboard | Login (password + "Sign in with Microsoft"), lead table, lead detail with similar leads, notifications bell, settings screens | |
| 7 | Reporting | Volume by source, category distribution, conversion funnel and rate, time to first response, high-value lead analytics | |
| 8 | **MCP server** ★ | Leads exposed as Model Context Protocol tools (search, get lead, list unanswered/critical, assign, change status, summary stats), mounted in the FastAPI app with the same auth and roles, so the sales team can work leads from Claude or ChatGPT. | |
| 9 | Hardening | E2E tests, rate limiting, security review, Sentry, prod Docker images, deployment to the Hetzner server, README with demo walkthrough | |

★ = newer technology chosen deliberately to show range. See `docs/TECH_STACK.md`.
