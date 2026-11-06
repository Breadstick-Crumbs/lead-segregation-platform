# Deployment

## Target: personal Hetzner server

The server was checked on 2026-11-06:

| Resource | State | Enough? |
|----------|-------|---------|
| CPU | 4 vCPU, load around 0 | Yes |
| Memory | 7.6 GB, 5.9 GB available, no swap | Yes. The stack needs about 1 GB. |
| Disk | 75 GB, **17 GB free (78% used)** | Yes. Our images and data come to about 2–3 GB. Docker holds about 55 GB in images, many of them unused or dangling, so pruning those would free a lot. |
| Docker | 29.1 + Compose 2.40 | Yes |
| Ingress | `flo-finance-caddy-1` owns ports 80/443 | Reuse it (below) |

### How it plugs in

The server already runs several apps the same way. We follow that pattern:

```
Browser / Meta webhook
   └─▶ leads.bricbybric.ae  (Cloudflare DNS record, DNS only)
         └─▶ Caddy (existing, :443, automatic Let's Encrypt)
               ├─ /api/*  ─▶ leads-api:8000
               └─ /*      ─▶ leads-web:80
                              leads-worker, leads-postgres (internal only)
```

- The app runs from `/opt/lead-platform` with a production compose file. The
  `api` and `web` services join the external `edge` network; Postgres stays on
  a private network and publishes no ports.
- Caddy routing lives in `/etc/caddy/sites/leads.caddy`, which the existing
  Caddyfile loads with `import /etc/caddy/sites/*.caddy`:

  ```caddy
  # Lead Segregation Platform, run from /opt/lead-platform (joins the edge network).
  # Caddy issues and renews the certificate itself (Cloudflare record is DNS only).
  leads.bricbybric.ae {
  	encode zstd gzip
  	reverse_proxy /api/* leads-api:8000 {
  		flush_interval -1   # stream SSE without buffering
  	}
  	reverse_proxy leads-web:80
  }
  ```

  Reload: `docker exec flo-finance-caddy-1 caddy reload --config /etc/caddy/Caddyfile`.
- WhatsApp webhook URL for Meta: `https://leads.bricbybric.ae/api/v1/webhooks/whatsapp`.
- Deploys: CI builds the images and pushes them to GHCR. The server pulls them
  and restarts with `docker compose pull && docker compose up -d`, then runs
  migrations with `docker compose run --rm api alembic upgrade head`.
- Backups: a nightly `pg_dump` cron job to `/opt/lead-platform/backups`,
  keeping 7 days.

This is set up in Phase 8 (Hardening), when there is an app worth deploying.

## Development webhook

Meta needs a public HTTPS URL even in development. Use a quick tunnel (no
account or domain needed; the URL changes on every run):

```bash
cloudflared tunnel --url http://localhost:8000
```

## Cloudflare-only hosting (not used)

Cloudflare Pages and Cloudflare Tunnel are free. The FastAPI API and worker
would need Cloudflare Containers, which require the Workers Paid plan
($5/month). Python Workers can't run the long-lived IMAP poller or job worker,
and there's no managed Postgres. The personal server avoids all of that.
