#!/usr/bin/env bash
# Start (or restart) the end-to-end stack and load the demo data. Needs Docker; on Windows
# run it inside WSL. Then: E2E_BASE_URL=http://localhost:8098 pnpm e2e
#   frontend/e2e/stack.sh        start and seed
#   frontend/e2e/stack.sh down   remove the stack and its data
set -euo pipefail
cd "$(dirname "$0")/../.."
compose=(docker compose -p lead-e2e -f docker-compose.yml -f docker-compose.e2e.yml)

if [[ "${1:-}" == "down" ]]; then
  "${compose[@]}" down -v
  exit 0
fi

# The worker would race the demo seed for its jobs, so it is stopped (on a re-run) and
# started last.
"${compose[@]}" up -d --build --wait postgres api web
"${compose[@]}" stop worker
"${compose[@]}" exec -T api alembic upgrade head
"${compose[@]}" exec -T api python -m app.scripts.seed
"${compose[@]}" exec -T api python -m app.scripts.seed_demo --reset
"${compose[@]}" up -d --wait worker
echo "E2E stack ready on http://localhost:8098"
