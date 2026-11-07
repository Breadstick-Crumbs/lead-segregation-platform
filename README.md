# Lead Segregation Platform

A web platform that collects sales leads from **email** and **WhatsApp**, uses AI to sort
and score them, replies to promising ones automatically, alerts the sales team about the
important ones, and reports on everything.

**Live demo:** https://leads.bricbybric.ae (reviewers get sign-in details separately)

---

## Contents

- [What it does](#what-it-does)
- [How a lead flows through the system](#how-a-lead-flows-through-the-system)
- [How accurate is the AI?](#how-accurate-is-the-ai)
- [Try it in 10 minutes](#try-it-in-10-minutes)
- [Demo walkthrough](#demo-walkthrough)
- [Use it from Claude or ChatGPT](#use-it-from-claude-or-chatgpt)
- [Technology](#technology)
- [Project layout](#project-layout)
- [Development and tests](#development-and-tests)
- [Deployment](#deployment)
- [Further documentation](#further-documentation)

---

## What it does

| Requirement | How the platform meets it |
|-------------|---------------------------|
| **Lead collection** | Email is read from company mailboxes over IMAP; WhatsApp messages arrive through the Meta Cloud API webhook, whose signature is verified. Each lead's source, timestamp and message are stored in PostgreSQL, and the same message is never stored twice. WhatsApp **voice notes are transcribed** (Arabic and English), so they are scored like text. |
| **Categorisation** | OpenAI classifies every lead as *Sales Opportunity*, *Demo Request*, *Partnership Inquiry*, *Support Request*, *General Inquiry* or *Spam*. Its answer must match a strict schema, and a labelled test set measures accuracy. |
| **Scoring (0–100)** | Six weighted factors: **intent**, **urgency** and **business value** (judged by the AI), plus **keywords**, **company information** and **previous interactions** (computed by rules). Admins can change the weights and preview the effect before saving. Every lead shows how its score was made. |
| **Automatic reply** | A lead scoring **above 20** gets an acknowledgement on the channel it came from: an email in the same thread, or a WhatsApp message. Templates exist in English and Arabic, and a lead can never be auto-replied twice. |
| **Dashboard** | Source, category, score, priority, status, owner and the full conversation and history for every lead, with filters, search and live updates. Sales reps and admins have different permissions. |
| **Notifications and escalation** | High-priority leads (score 60+) alert their owner in the app and by email. Critical leads (80+) are also escalated to managers and Slack or Teams. A high-priority lead left unanswered for 60 minutes is escalated again. |
| **Reporting** | Lead volume by source, category distribution, the conversion funnel and rate, time to first response, and high-value lead analytics, over 7, 30 or 90 days. |

**Beyond the brief**

- **Try a lead:** paste any message, in English or Arabic, and watch the real pipeline
  classify and score it with OpenAI, live. It shows the reply it would send and who it
  would alert, and stores nothing.
- **AI assistant access:** the sales team can connect Claude or ChatGPT and ask things
  like *"Which critical WhatsApp leads are unanswered?"*, then assign or update leads from
  the chat. It uses the same sign-in and permissions as the dashboard (Model Context
  Protocol).
- **Sign in with Microsoft** (Entra ID), next to email and password.
- **Similar past leads and duplicate detection** using vector search (pgvector).
- **Integrations set up in the browser:** WhatsApp, Slack, Teams and Microsoft sign-in
  are configured in Settings, with a Test button for each. No server access needed.
- **Production-ready:** rate limiting, a security review, error monitoring (Sentry),
  automated browser tests in CI, non-root containers, HTTPS and nightly backups.

---

## How a lead flows through the system

```
 Email (IMAP)  ─┐
                ├─▶ stored ─▶ AI classifies ─▶ score 0–100 ─▶ assigned to a rep
 WhatsApp     ──┘                                  │
                                                   ├─ score > 20  ─▶ automatic reply
                                                   ├─ score ≥ 60  ─▶ owner notified
                                                   └─ score ≥ 80  ─▶ escalated (managers, Slack/Teams)

 Dashboard: work the lead (reply, note, status, owner)  ·  Reports  ·  Claude / ChatGPT
```

| Score | Priority | What happens |
|-------|----------|--------------|
| 0–20 | Low | Stored; no automatic reply |
| 21–59 | Medium | Automatic reply |
| 60–79 | High | Automatic reply and the owner is notified |
| 80–100 | Critical | Automatic reply, owner notified and escalated |

Spam is capped at a score of 5, so it is never replied to or alerted. All thresholds are
editable in Settings. The full model is in [docs/SCORING.md](docs/SCORING.md).

---

## How accurate is the AI?

The classifier is measured against 53 hand-labelled leads covering every category, in
English and Arabic, by email and WhatsApp, including two prompt-injection attempts. It
uses the real OpenAI model and the production prompt:

| Measure | Result |
|---------|--------|
| Category accuracy | **98%** (52/53) |
| Arabic leads | 100% (13/13) |
| Language detected correctly | 100% |
| Score in the expected band | 100% |
| Spam caught | 100% (10/10) |
| Prompt injections resisted | 2/2 |
| Median time per lead | 3.2 s |

The one miss was a "please unsubscribe me" email, labelled General and classified as
Support. The full report, with per-category precision and recall, a confusion matrix and
the model's reasoning for the miss, is in
[docs/CLASSIFIER_EVAL.md](docs/CLASSIFIER_EVAL.md). Regenerate it after any prompt or
model change with `uv run python -m tests.live.evaluation` (in `backend/`).

---

## Try it in 10 minutes

**You need:** Docker Desktop (with Compose 2.24 or newer) and Git.

```bash
git clone https://github.com/Breadstick-Crumbs/lead-segregation-platform.git
cd lead-segregation-platform
cp .env.example backend/.env
```

Open `backend/.env` and choose two passwords:

```env
SEED_ADMIN_PASSWORD=choose-an-admin-password
SEED_DEMO_PASSWORD=choose-a-rep-password
```

`OPENAI_API_KEY` is optional for the demo: the sample leads come with their AI analysis
already written. Add it to use **Try a lead** and to score new leads live. Then start
everything and load the sample data:

```bash
docker compose up -d --build
docker compose exec api alembic upgrade head          # create the database tables
docker compose exec api python -m app.scripts.seed    # settings, admin and sales reps
docker compose stop worker                            # pause background jobs while seeding
docker compose exec api python -m app.scripts.seed_demo
docker compose exec api python -m app.scripts.reembed_leads   # only with an OpenAI key
docker compose start worker
```

The `reembed_leads` step gives the sample leads real OpenAI embeddings, so similar-lead search
works on new messages too.

Open **http://localhost:8088** and sign in:

| Account | Email | Password |
|---------|-------|----------|
| Admin | `admin@tawasol.test` | your `SEED_ADMIN_PASSWORD` |
| Sales reps | `layla.hassan@tawasol.test`, `omar.khalid@tawasol.test`, `priya.nair@tawasol.test` | your `SEED_DEMO_PASSWORD` |

The sample data is 17 realistic Gulf-region enquiries in English and Arabic, by email and
WhatsApp, covering every category. They go through the real pipeline (storage, scoring,
assignment, alerts), with only the OpenAI call replaced by a prepared analysis.

To stop: `docker compose down`. To start over: `docker compose down -v` and repeat.

---

## Demo walkthrough

1. **Sign in as the admin.** The home screen shows the numbers for the last 24 hours and 30
   days, then **Needs you now** (open high and critical leads), then the full lead table.
   Filters and search are kept in the URL, so a view can be bookmarked or shared.
2. **Open a critical lead.** The score card breaks the score into its six factors and shows
   the matched keywords and the AI's reasoning. Below that are the conversation (the
   automatic reply is labelled), similar past leads and the full history.
3. **Change the status**, for example to *Contacted*, and save. This counts as the first
   response, which stops the 60-minute escalation timer. The change appears in the history.
4. **Reply, or add an internal note.** A reply goes out on the lead's own channel, in the
   same thread. WhatsApp only allows free-form replies within 24 hours of the customer's
   last message, and the screen says when that window has closed.
5. **Open Try a lead** and pick an example, or paste your own message in any language.
   In a few seconds you see the category, the score with each factor, the AI's
   reasoning, the reply it would send, who would be alerted, and similar past leads.
   Nothing is saved.
6. **Watch the bell** in the top bar. Alerts arrive live and also pop up as toasts.
7. **Open Reports:** volume by source, category mix, conversion funnel, time to first
   response and high-value leads. Each figure is defined in
   [docs/REPORTING.md](docs/REPORTING.md).
8. **Open Settings** (admins only):
   - **Scoring:** move a weight and the latest 20 leads are rescored on the spot.
   - **Keywords**, **Auto-reply** templates (English and Arabic), **Alerts**, **Mailboxes**
     and **Users**.
   - **Integrations:** connect WhatsApp, Slack, Teams and Microsoft sign-in. Each has a
     **Test** button and shows the exact URLs to paste into Meta or Microsoft. Secrets are
     encrypted and never shown again.
9. **Sign in as a sales rep** in a private window. Reps work every lead and can claim
   unassigned ones. Only admins can reassign leads or open Settings.

---

## Use it from Claude or ChatGPT

The platform runs an MCP server, so an AI assistant can search and work leads for you.

1. In the dashboard, open **AI apps** and copy the server URL. On the live site it is
   `https://leads.bricbybric.ae/mcp`.
2. Add it to your assistant:
   - **Claude:** Settings → Connectors → *Add custom connector*.
   - **ChatGPT:** add it as an app in developer mode.
   - **Claude Code:** `claude mcp add --transport http tawasol <url>`.
3. A browser window opens the platform's consent page. Sign in, check which app is asking,
   and choose **Allow access**.
4. Ask, for example:
   - *"Which critical WhatsApp leads are still unanswered?"*
   - *"Summarise this week's leads by category."*
   - *"Assign the newest quote request to Layla and mark it contacted."*

The assistant acts with your permissions. **AI apps** lists connected assistants, and
**Disconnect** removes one at once. Claude and ChatGPT on the web need the public HTTPS
address; Claude Code and the desktop apps can also use `http://localhost:8088/mcp`.
Details: [docs/MCP.md](docs/MCP.md).

---

## Technology

| Part | Choice |
|------|--------|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2 (async), Pydantic |
| Database and jobs | PostgreSQL 17 with pgvector; Procrastinate for background jobs in Postgres (no Redis) |
| AI | OpenAI: structured classification, speech-to-text for voice notes, embeddings |
| Channels | IMAP and SMTP for email; Meta WhatsApp Cloud API |
| Frontend | React 19, TypeScript, Vite, TanStack Router and Query, Tailwind CSS, Recharts |
| Live updates | Server-sent events over Postgres LISTEN/NOTIFY |
| Sign-in | JWT with rotating refresh tokens; Microsoft Entra ID (OpenID Connect); OAuth 2.1 for AI assistants |
| Hosting | Docker Compose, nginx, Caddy (HTTPS) on a Hetzner server |
| Quality | Ruff, mypy (strict), pytest with a real Postgres, Vitest, Playwright, GitHub Actions |

Why each was chosen: [docs/TECH_STACK.md](docs/TECH_STACK.md).

---

## Project layout

```
backend/            FastAPI app
  app/api/          HTTP routes (thin)
  app/services/     business logic: pipeline, scoring, replies, notifications, reports
  app/repositories/ database access
  app/integrations/ OpenAI, WhatsApp, IMAP/SMTP, Slack/Teams, Microsoft
  app/mcp_server/   the MCP server for AI assistants
  app/workers/      background jobs
  migrations/       Alembic database migrations
  tests/            unit and integration tests
frontend/           React dashboard
  src/features/     screens: leads, reports, settings, notifications, sign-in, AI apps
  e2e/              Playwright browser tests
deploy/             production Compose file, Caddy site, env templates, backup script
docs/               design and operations documentation
```

---

## Development and tests

To run the parts outside Docker while developing (needs uv, Node 22 and pnpm):

```bash
docker compose up -d postgres
cd backend
uv sync && uv run alembic upgrade head && uv run python -m app.scripts.seed
uv run fastapi dev app/main.py                              # API on :8000, docs at /docs
uv run procrastinate --app=app.workers.worker.app worker    # background jobs (second terminal)
cd ../frontend
pnpm install && pnpm dev                                    # dashboard on :5173
```

**Checks** (all run in CI on every change):

| Area | Command |
|------|---------|
| Backend (in `backend/`) | `uv run ruff check . && uv run ruff format --check . && uv run mypy app tests && uv run pytest` |
| Frontend (in `frontend/`) | `pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build` |
| Browser tests | `bash frontend/e2e/stack.sh` (on Windows, inside WSL), then in `frontend/`: `pnpm exec playwright install chromium && pnpm e2e` |

The browser tests drive Chromium against a separate Docker stack on port 8098, so they
don't touch a running dev stack. They cover:

- signing in, signing out and following deep links;
- opening a lead and changing its status;
- a new WhatsApp lead arriving through a signed webhook and getting a reply;
- the reports page.

Remove that stack with `bash frontend/e2e/stack.sh down`.

---

## Deployment

Production runs at https://leads.bricbybric.ae on a Hetzner server. Caddy handles HTTPS,
and the app runs in non-root containers. Database migrations run before each release
starts, and backups run nightly. The step-by-step runbook, including updates, rollback and
restoring a backup, is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Further documentation

| Topic | Document |
|-------|----------|
| Architecture and data model | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Classification and scoring | [docs/SCORING.md](docs/SCORING.md) |
| Email and WhatsApp intake, Meta and mailbox setup | [docs/INGESTION.md](docs/INGESTION.md) |
| AI pipeline, Try a lead, and how the classifier is evaluated | [docs/AI_PIPELINE.md](docs/AI_PIPELINE.md) |
| Classifier evaluation results | [docs/CLASSIFIER_EVAL.md](docs/CLASSIFIER_EVAL.md) |
| Automatic replies | [docs/AUTO_REPLY.md](docs/AUTO_REPLY.md) |
| Notifications and escalation | [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md) |
| Dashboard screens and permissions | [docs/DASHBOARD.md](docs/DASHBOARD.md) |
| Report definitions | [docs/REPORTING.md](docs/REPORTING.md) |
| AI assistant access (MCP) | [docs/MCP.md](docs/MCP.md) |
| Sign in with Microsoft | [docs/MICROSOFT_SIGN_IN.md](docs/MICROSOFT_SIGN_IN.md) |
| Security, rate limits and error monitoring | [docs/SECURITY.md](docs/SECURITY.md) |
| Deployment runbook | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Technology choices | [docs/TECH_STACK.md](docs/TECH_STACK.md) |
| Roadmap | [docs/ROADMAP.md](docs/ROADMAP.md) |
| Decisions and assumptions | [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) |
