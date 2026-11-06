# Tech stack and tooling

Versions were verified against PyPI/npm on 2026-11-06. The lockfiles
(`uv.lock`, `pnpm-lock.yaml`) are the source of truth after scaffolding.

## Backend (Python 3.11, managed with uv)

| Concern | Choice | Why |
|---------|--------|-----|
| Web framework | FastAPI 0.162 | Async, OpenAPI generation, dependency injection |
| Validation/config | Pydantic 2.14 + pydantic-settings | Typed schemas and env-based settings |
| ORM | SQLAlchemy 2.1 (async, `Mapped[]`) + asyncpg | Mature, typed, async |
| Migrations | Alembic 1.20 (async env) | Standard for SQLAlchemy |
| Background jobs | **Procrastinate 3.10** (Postgres-backed) | No Redis; enqueue in the same transaction as the lead row; retries, locks and periodic tasks built in. (ARQ is maintenance-only; Celery isn't async-native.) |
| Real-time | SSE (`sse-starlette`) + Postgres LISTEN/NOTIFY | One-way server→browser push without WebSocket infrastructure |
| Auth | PyJWT 2.15 + pwdlib[argon2] 0.3 | What FastAPI's docs now recommend. python-jose and passlib are stale; fastapi-users is maintenance-only. |
| AI | openai-python 3.x, Responses API, `responses.parse(text_format=Model)` | Structured outputs validated as Pydantic. Model ID from config (default `gpt-6-luna`, `reasoning.effort=low`). |
| Email in | imap-tools 1.16 (UID polling in a worker thread) | Works with any IMAP server; dedup on Message-ID |
| Email out | aiosmtplib 5.1 | Async SMTP with threading headers |
| Mail auth | Per-mailbox strategy: `password` or `oauth2_xoauth2` | Microsoft 365 IMAP basic auth is gone and SMTP basic auth is off by default from Dec 2026; Google prefers OAuth |
| WhatsApp | Meta Cloud API over httpx; Graph version from config (v26.0) | Official, webhook-based, free inbound |
| HTTP client | httpx 0.28 | Async; also used as the test client |
| Logging | structlog (JSON in prod, request-id contextvar) | Structured, correlatable logs |
| Errors | sentry-sdk (FastAPI integration), off when DSN unset | Error tracking |
| Secrets at rest | `cryptography` Fernet for mailbox/OAuth secrets | Credentials never stored in plaintext |
| Lint/format | ruff 0.17 | One tool for lint and format |
| Types | mypy 2.x strict + pydantic plugin | Catch bugs before runtime |
| Tests | pytest 9 + pytest-asyncio 1.x + httpx ASGITransport; Postgres via Docker | Real database in integration tests, no SQLite mismatch |

## Frontend (pnpm)

| Concern | Choice |
|---------|--------|
| Framework | React 19 + Vite 8 + TypeScript (strict) |
| Routing | TanStack Router (typed search params for the filter-heavy lead table) |
| Server state | TanStack Query 5 |
| API client | orval: TanStack Query hooks generated from FastAPI `/openapi.json` |
| UI | shadcn/ui + Tailwind CSS v4 |
| Tables | TanStack Table |
| Charts | Recharts 3 |
| Forms | react-hook-form + zod |
| Lint/format | oxlint + Prettier |
| Tests | Vitest + Testing Library; Playwright for E2E |

TypeScript 7 (the Go-native compiler) ships with the Vite template; oxlint avoids
typescript-eslint compatibility issues.

## Infrastructure and quality gates

- **Docker Compose:** `postgres`, `api`, `worker`, `web`. Docker Desktop is needed;
  it isn't installed on the dev machine yet.
- **ngrok** (or Cloudflare Tunnel) exposes the WhatsApp webhook in development.
- **pre-commit:** ruff, ruff-format, mypy, prettier, oxlint, end-of-file and
  large-file checks.
- **GitHub Actions:** backend (uv sync → ruff → mypy → alembic upgrade → pytest
  against a Postgres service container) and frontend (typecheck → lint → test
  → build).
- **Conventional Commits** and short-lived feature branches with PRs.

## MCP servers for development (Claude Code)

| MCP | Use | Install |
|-----|-----|---------|
| Context7 | Current docs for FastAPI, SQLAlchemy, TanStack, shadcn | `claude mcp add context7 -- npx -y @upstash/context7-mcp` |
| OpenAI Docs (official) | Responses API and structured-outputs reference | `claude mcp add --transport http openaiDeveloperDocs https://developers.openai.com/mcp` |
| Playwright | Drive the dashboard, E2E checks, screenshots | `claude mcp add playwright npx @playwright/mcp@latest` |
| GitHub | PRs, issues, CI status (`gh` CLI also works) | `claude mcp add --transport http github https://api.githubcopilot.com/mcp/ --header "Authorization: Bearer <PAT>"` |
| Postgres (dev DB only, restricted) | Inspect schema and data while debugging | `claude mcp add postgres -e DATABASE_URI=postgresql://... -- uvx postgres-mcp --access-mode=restricted` |
| Sentry (later) | Triage production errors | `claude mcp add --transport http sentry https://mcp.sentry.dev/mcp/<org>/<project>` |

Don't use the archived `@modelcontextprotocol/server-postgres`: it is deprecated
and its read-only mode has a reported SQL-injection bypass.

## Claude Code skills already available

| Skill | When |
|-------|------|
| `taste-ui` | Designing the dashboard screens |
| `dataviz` | The reporting charts |
| `code-review` / `simplify` | Before each PR |
| `security-review` | Auth, webhooks, credential storage |
| `run` | Launching and checking the app |
