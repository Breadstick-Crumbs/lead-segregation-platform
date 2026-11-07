# Deployment

Production runs on the personal Hetzner server at **https://leads.bricbybric.ae**, behind
the Caddy that already serves the server's other apps. Everything needed is in `deploy/`.
The runbook below is step by step; nothing in it has been run against the server yet.

## Target: personal Hetzner server

The server was checked on 2026-11-06:

| Resource | State | Enough? |
|----------|-------|---------|
| CPU | 4 vCPU, load around 0 | Yes |
| Memory | 7.6 GB, 5.9 GB available, no swap | Yes. The stack needs about 1 GB. |
| Disk | 75 GB, **17 GB free (78% used)** | Yes. Our images and data come to about 2–3 GB. Docker holds about 55 GB in images, many of them unused or dangling, so pruning those would free a lot. |
| Docker | 29.1 + Compose 2.40 | Yes |
| Ingress | `flo-finance-caddy-1` owns ports 80/443 | Reuse it (below) |

## How it fits together

```
Browser / Meta webhook / Claude & ChatGPT
   └─▶ leads.bricbybric.ae   (Cloudflare DNS A record, DNS only, not proxied)
         └─▶ Caddy  (existing flo-finance-caddy-1, :443, automatic Let's Encrypt, HSTS)
               ├─ /api/*  ─▶ leads-api:8000   (FastAPI, uvicorn)
               └─ /*      ─▶ leads-web:8080   (nginx: the SPA, security headers;
                                               forwards /mcp and the OAuth paths to the API)
   internal network only:  worker (Procrastinate), postgres (pgvector), migrate (one-shot)
```

| File | What it is |
|------|------------|
| `deploy/docker-compose.prod.yml` | The stack. `api` and `web` join the external `edge` network as `leads-api` and `leads-web`. Everything else is on `internal`, and nothing publishes a port. |
| `deploy/leads.caddy` | The Caddy site block: TLS, HSTS, routing, unbuffered SSE. |
| `deploy/compose.env.example` | Compose variables: the Postgres password, image names and tag, and the browser Sentry DSN. |
| `deploy/backend.env.example` | App settings for the API and worker. |
| `deploy/backup.sh` | Nightly `pg_dump`, keeping 7 days. |
| `.github/workflows/images.yml` | Pushes both images to GHCR on every push to `main`, tagged with the commit and `latest`. |

**Images:** both are multi-stage builds.

- **API:** `python:3.11-slim` with only the locked virtualenv. It runs as uid 10001 and has
  a healthcheck on `/api/v1/health/live`.
- **Web:** `nginx-unprivileged`, listening on 8080, with a healthcheck.

Both containers run read-only, with no capabilities and `no-new-privileges`. A one-shot
`migrate` service runs `alembic upgrade head` before the API and worker start, so a deploy
can never serve a schema it doesn't have.

## What you provide

1. **SSH access** to the server as a user who can run Docker and write `/opt` and
   `/etc/caddy/sites`.
2. **DNS:** an `A` record `leads` → the server's public IP in the `bricbybric.ae` Cloudflare
   zone. Set it to **DNS only** (grey cloud), so Caddy can complete the Let's Encrypt
   challenge.
3. **Secrets:** the OpenAI key, plus whichever of WhatsApp, Microsoft sign-in,
   Slack/Teams and Sentry you want on. All of them except the OpenAI key can also be set
   later in the dashboard.

## Runbook

All commands run on the server unless marked otherwise.

### 1. Check the ground

```bash
docker network ls | grep edge                     # the network Caddy's stack created
docker inspect flo-finance-caddy-1 --format '{{json .NetworkSettings.Networks}}' | grep -o '"edge"'
grep -n 'import /etc/caddy/sites' /etc/caddy/Caddyfile   # site files are loaded
dig +short leads.bricbybric.ae                    # must print this server's IP
df -h / && docker system df                       # free space; `docker image prune` if tight
```

If the network that Caddy uses has a different name, change `edge` in
`deploy/docker-compose.prod.yml` (the `networks.edge` entry) to match. If the Caddyfile has
no `import` line, paste the contents of `deploy/leads.caddy` into it instead of step 4.

### 2. Get the code and write the settings

```bash
sudo mkdir -p /opt/lead-platform && sudo chown "$USER" /opt/lead-platform
git clone https://github.com/Breadstick-Crumbs/lead-segregation-platform.git /opt/lead-platform
cd /opt/lead-platform
cp deploy/compose.env.example deploy/compose.env
cp deploy/backend.env.example deploy/backend.env
chmod 600 deploy/*.env
```

Fill in `deploy/compose.env`:
- `POSTGRES_PASSWORD`: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

Fill in `deploy/backend.env`:
- `JWT_SECRET`: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
- `SECRETS_ENCRYPTION_KEY`: generate it inside the image, so the host needs nothing
  installed. Build first if needed (step 3):
  `docker run --rm lead-platform-api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- `OPENAI_API_KEY`.
- `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD`, and `SEED_DEMO_PASSWORD` if you want the
  demo reps.
- Anything optional: WhatsApp, Entra, Slack/Teams, `SENTRY_DSN`.

`FRONTEND_URL` and `CORS_ORIGINS` are already set to `https://leads.bricbybric.ae`.

**Keep `SECRETS_ENCRYPTION_KEY` safe.** Without it, saved mailbox passwords and
integration secrets can't be decrypted.

### 3. Build and start

```bash
cd /opt/lead-platform/deploy
docker compose -f docker-compose.prod.yml --env-file compose.env up -d --build --wait
docker compose -f docker-compose.prod.yml --env-file compose.env ps
```

All services show `healthy`, and `migrate` shows `exited (0)`. The build takes a few
minutes the first time.

To use CI-built images instead of building on the server:

1. Set `IMAGE_PREFIX=ghcr.io/breadstick-crumbs/lead-platform` and `IMAGE_TAG=<commit sha>`
   in `compose.env`.
2. If the packages are private, run `docker login ghcr.io` with a read-only token.
3. Run `docker compose ... pull` before `up -d` (without `--build`).

### 4. Route the domain

```bash
sudo cp /opt/lead-platform/deploy/leads.caddy /etc/caddy/sites/leads.caddy
docker exec flo-finance-caddy-1 caddy validate --config /etc/caddy/Caddyfile
docker exec flo-finance-caddy-1 caddy reload --config /etc/caddy/Caddyfile
```

If `/etc/caddy/sites` on the host isn't the directory mounted into the container, copy the
file to the mounted path instead. `docker inspect flo-finance-caddy-1 --format '{{json .Mounts}}'`
shows the mounts.

### 5. Seed

```bash
cd /opt/lead-platform/deploy
C="docker compose -f docker-compose.prod.yml --env-file compose.env"
$C run --rm api python -m app.scripts.seed            # settings, admin, demo reps
# Optional, for the interview demo only (it refuses in production without the flag):
$C stop worker
$C run --rm api python -m app.scripts.seed_demo --allow-production
$C start worker
```

### 6. Verify

Run these from your own machine:

```bash
curl -sI https://leads.bricbybric.ae | grep -iE 'HTTP/|strict-transport|content-security'
curl -s https://leads.bricbybric.ae/api/v1/health/ready        # {"status":"ok","database":"ok"}
curl -s https://leads.bricbybric.ae/.well-known/oauth-authorization-server | head -c 200
curl -s -o /dev/null -w '%{http_code}\n' https://leads.bricbybric.ae/openapi.json   # 404 in production
```

Then sign in as the seed admin, open a lead, and check Settings → Integrations.

### 7. Point the integrations at it

- **Meta:** set the webhook callback URL to
  `https://leads.bricbybric.ae/api/v1/webhooks/whatsapp` with the verify token from
  Settings → Integrations (or `.env`), and subscribe to `messages`.
- **Entra:** add the redirect URI `https://leads.bricbybric.ae/api/v1/auth/microsoft/callback`
  (see [MICROSOFT_SIGN_IN.md](MICROSOFT_SIGN_IN.md)).
- **Claude / ChatGPT:** connect to `https://leads.bricbybric.ae/mcp` (see [MCP.md](MCP.md)).

### 8. Backups

```bash
chmod +x /opt/lead-platform/deploy/backup.sh
/opt/lead-platform/deploy/backup.sh               # try it once
( crontab -l 2>/dev/null; echo '15 3 * * * /opt/lead-platform/deploy/backup.sh >> /var/log/lead-platform-backup.log 2>&1' ) | crontab -
```

To restore a dump:

```bash
docker compose -f docker-compose.prod.yml --env-file compose.env exec -T postgres \
  pg_restore -U leads -d leads --clean < backups/<file>.dump
```

Copy `backups/` off the server too (for example with a Hetzner Storage Box and `rsync`). A
backup that lives only on the same disk doesn't survive losing that disk.

## Updating

Building on the server:

```bash
cd /opt/lead-platform && git pull --ff-only
cd deploy && docker compose -f docker-compose.prod.yml --env-file compose.env up -d --build --wait
```

With CI-built images: set `IMAGE_TAG` in `compose.env` to the new commit, then run this
(never `--build`, which would label local code with that tag):

```bash
cd /opt/lead-platform && git pull --ff-only     # compose file and Caddy config changes
cd deploy && docker compose -f docker-compose.prod.yml --env-file compose.env pull
docker compose -f docker-compose.prod.yml --env-file compose.env up -d --wait
```

Either way, `migrate` runs before the new API and worker start.

**Rolling back.** An older image doesn't know a newer migration, so its `migrate` would
fail with "Can't locate revision" and keep the API from starting. Roll back without it.
Set `IMAGE_TAG` to the previous commit (or check that commit out and build), then start
only the app containers:

```bash
docker compose -f docker-compose.prod.yml --env-file compose.env up -d --no-deps --wait api worker web
```

Migrations are written to be additive, so the previous version runs against the newer
schema. To undo a migration itself, run `alembic downgrade -1` with the *newer* image
before rolling back.

## Logs and errors

- `docker compose -f docker-compose.prod.yml --env-file compose.env logs -f api worker`
  shows JSON lines with a `request_id` on every API line. Each container's logs rotate at
  10 MB × 5.
- With `SENTRY_DSN` and `VITE_SENTRY_DSN` set, errors go to Sentry. See
  [SECURITY.md](SECURITY.md#error-reporting-sentry).

## Development webhook

Meta needs a public HTTPS URL even in development. Use a quick tunnel (no account or
domain needed; the URL changes on every run):

```bash
cloudflared tunnel --url http://localhost:8000
```

## Cloudflare-only hosting (not used)

Cloudflare Pages and Cloudflare Tunnel are free. The FastAPI API and worker would need
Cloudflare Containers, which require the Workers Paid plan ($5/month). Python Workers can't
run the long-lived IMAP poller or job worker, and there's no managed Postgres. The personal
server avoids all of that.
