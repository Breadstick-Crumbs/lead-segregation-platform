# Lead Segregation Platform

Captures leads from company email (IMAP) and WhatsApp (Meta Cloud API),
classifies and scores them with OpenAI, auto-replies through the same channel
when the score is above 20, notifies and escalates high-priority leads, and
shows everything on a dashboard with reporting.

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Scoring model: [docs/SCORING.md](docs/SCORING.md)
- Stack and tooling: [docs/TECH_STACK.md](docs/TECH_STACK.md)
- Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)
- Ingestion and Meta/mailbox setup: [docs/INGESTION.md](docs/INGESTION.md)
- AI pipeline, guarantees and classifier evaluation: [docs/AI_PIPELINE.md](docs/AI_PIPELINE.md)
- Auto-reply (email and WhatsApp), at-most-once sending, delivery receipts: [docs/AUTO_REPLY.md](docs/AUTO_REPLY.md)
- Notifications, escalation, SLA and the live stream: [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md)
- Dashboard screens, permissions and demo data: [docs/DASHBOARD.md](docs/DASHBOARD.md)

## Quick start

Requires Docker Desktop, uv, Node 22 and pnpm.

```bash
cp .env.example backend/.env
docker compose up -d postgres
cd backend && uv sync && uv run alembic upgrade head && uv run python -m app.scripts.seed
uv run fastapi dev app/main.py
uv run procrastinate --app=app.workers.app.app worker   # second terminal: background jobs
cd frontend && pnpm install && pnpm dev
```

The API runs at http://localhost:8000 (docs at `/docs`) and the dashboard at http://localhost:5173.

The full stack in containers: `docker compose up --build`, dashboard at http://localhost:8088.

For a dashboard full of realistic leads, run the demo seed (see [docs/DASHBOARD.md](docs/DASHBOARD.md#demo-data)).
