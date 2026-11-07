# Lead Segregation Platform

The platform captures leads from company email (IMAP) and WhatsApp (Meta Cloud API) and
classifies and scores them with OpenAI. When a lead scores above 20 it gets an automatic
reply on the same channel. High-priority leads trigger notifications and escalate, and a
dashboard shows everything with reporting. Sales can also work the leads from Claude or
ChatGPT through an MCP server.

**Live:** https://leads.bricbybric.ae (once deployed; see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md))

## Demo walkthrough (about 10 minutes)

### 1. Start it with demo data

You need Docker (Compose 2.24+), uv, Node 22 and pnpm.

```bash
cp .env.example backend/.env       # set SEED_ADMIN_PASSWORD and SEED_DEMO_PASSWORD;
                                   # OPENAI_API_KEY is optional for the demo
docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose exec api python -m app.scripts.seed
docker compose stop worker         # it would race the demo seed for the leads' jobs
docker compose exec api python -m app.scripts.seed_demo
docker compose start worker
```

Open http://localhost:8088. The seed creates these accounts:

| Account | Email | Password |
|---------|-------|----------|
| Admin | `admin@tawasol.test` | `SEED_ADMIN_PASSWORD` |
| Sales reps | `layla.hassan@tawasol.test`, `omar.khalid@tawasol.test`, `priya.nair@tawasol.test` | `SEED_DEMO_PASSWORD` |

`seed_demo` puts 17 realistic GCC enquiries through the real ingestion, scoring, assignment
and alerting code. They are in English and Arabic, by email and WhatsApp, and cover every
category. Only the OpenAI calls are replaced, by a scripted analysis.

### 2. What to click

1. **Sign in** as the admin. The home screen shows stats for the last 24 hours and 30 days.
   **Needs you now** lists the open high and critical leads. The full table below it is
   filterable and searchable, and the URL keeps the view, so it can be shared.
2. **Open a critical lead.** The score card breaks the score into its factors (intent,
   budget, urgency, fit, previous interactions) and shows the matched keywords and the AI's
   rationale. Further down are the conversation, with the auto-reply labelled; similar past
   leads, found with pgvector embeddings; and the full history.
3. **Change the status** (for example to *Contacted*) and save. That records the first
   response, which stops the SLA escalation timer. The change appears in the history.
4. **Reply or add an internal note.** A reply goes out on the lead's own channel, threaded
   under the customer's last message. WhatsApp only allows free-form replies within 24
   hours of the customer's last message, and the composer says when that window has closed.
5. **Watch the bell.** Alerts arrive live over SSE, and each one shows as a toast.
6. **Reports:** volume by source, category mix, the conversion funnel and rate, time to
   first response, and high-value leads. You can switch between 7, 30 and 90 days. Every
   figure is defined in [docs/REPORTING.md](docs/REPORTING.md).
7. **Settings** (admin only):
   - **Scoring:** change a weight and the last 20 leads are rescored live.
   - **Keywords**, **Auto-reply** templates per language, **Alerts**, **Mailboxes** and
     **Users**.
   - **Integrations:** set up WhatsApp, Slack, Teams and Microsoft sign-in in the browser.
     Each has a **Test** button and shows the URLs to paste into Meta or Entra. Values saved
     here override `.env`, are encrypted at rest, and are never shown again.
8. **Sign in as a rep** in a private window. Reps can work every lead and claim unassigned
   ones, but they can't reassign leads or see Settings.

### 3. Connect Claude or ChatGPT (MCP)

1. Open **AI apps** and copy the server URL (`https://leads.bricbybric.ae/mcp` when
   deployed).
2. Add it to your AI app:
   - **Claude:** Settings → Connectors → *Add custom connector*.
   - **ChatGPT:** an app in developer mode.
   - **Claude Code:** `claude mcp add --transport http tawasol <url>`.
3. The app opens the dashboard's consent page. Sign in, check which app is asking and where
   it will return, and choose **Allow access**.
4. Ask, for example, "Which critical WhatsApp leads are unanswered?" or "Assign the newest
   quote request to Layla and mark it contacted." The tools run with your role's
   permissions. **AI apps** lists the connection, and **Disconnect** ends it.

Claude and ChatGPT on the web connect from their own servers, so they need the deployed
https URL. Claude Code and the desktop apps can use `http://localhost:8088/mcp`. Details are
in [docs/MCP.md](docs/MCP.md).

## Development

```bash
cp .env.example backend/.env
docker compose up -d postgres
cd backend && uv sync && uv run alembic upgrade head && uv run python -m app.scripts.seed
uv run fastapi dev app/main.py                                  # API on :8000, docs at /docs
uv run procrastinate --app=app.workers.worker.app worker        # second terminal: jobs
cd frontend && pnpm install && pnpm dev                         # dashboard on :5173
```

### Checks

Every PR runs these in CI:

- **Backend** (in `backend/`): `uv run ruff check . && uv run ruff format --check . && uv run mypy app tests && uv run pytest`.
  Integration tests need the Compose Postgres.
- **Frontend** (in `frontend/`): `pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build`.
- **End to end:** Playwright drives the main flows in Chromium against the Docker Compose
  stack: signing in and out, opening a lead, changing its status, and a new WhatsApp lead
  arriving through a signed webhook and getting a reply. It also covers reports and deep
  links. The E2E stack runs beside a dev stack on port 8098 and is started with:

  ```bash
  bash frontend/e2e/stack.sh              # on Windows, inside WSL; builds, migrates, seeds
  cd frontend && pnpm exec playwright install chromium && pnpm e2e
  bash frontend/e2e/stack.sh down         # remove it and its data
  ```

## Documentation

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Scoring model: [docs/SCORING.md](docs/SCORING.md)
- Stack and tooling: [docs/TECH_STACK.md](docs/TECH_STACK.md)
- Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)
- Ingestion and Meta/mailbox setup: [docs/INGESTION.md](docs/INGESTION.md)
- AI pipeline, guarantees and classifier evaluation: [docs/AI_PIPELINE.md](docs/AI_PIPELINE.md)
- Auto-reply (email and WhatsApp), at-most-once sending, delivery receipts: [docs/AUTO_REPLY.md](docs/AUTO_REPLY.md)
- Notifications, escalation, SLA and the live stream: [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md)
- Dashboard screens, permissions and demo data: [docs/DASHBOARD.md](docs/DASHBOARD.md)
- Reporting: what each figure means: [docs/REPORTING.md](docs/REPORTING.md)
- MCP server: working leads from Claude or ChatGPT, OAuth sign-in: [docs/MCP.md](docs/MCP.md)
- Security: controls, rate limits, Sentry, review findings: [docs/SECURITY.md](docs/SECURITY.md)
- Deployment: production images, Caddy, runbook, backups: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
