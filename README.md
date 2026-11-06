# Lead Segregation Platform

Captures leads from company email (IMAP) and WhatsApp (Meta Cloud API),
classifies and scores them with OpenAI, auto-replies through the same channel
when the score is above 20, notifies and escalates high-priority leads, and
shows everything on a dashboard with reporting.

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Scoring model: [docs/SCORING.md](docs/SCORING.md)
- Stack and tooling: [docs/TECH_STACK.md](docs/TECH_STACK.md)
- Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)

## Quick start

Requires Docker Desktop, uv, Node 22 and pnpm.

```bash
cp .env.example backend/.env
docker compose up -d postgres
cd backend && uv sync && uv run alembic upgrade head && uv run fastapi dev app/main.py
cd frontend && pnpm install && pnpm dev
```

The API runs at http://localhost:8000 (docs at `/docs`) and the dashboard at http://localhost:5173.

The full stack in containers: `docker compose up --build`, dashboard at http://localhost:8088.
