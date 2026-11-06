# Deployment

## What Cloudflare can and can't host for free

| Part | Free on Cloudflare? | Notes |
|------|---------------------|-------|
| React dashboard | **Yes**: Cloudflare Pages | Static build, global CDN, free HTTPS on `*.pages.dev` or a custom domain |
| Public HTTPS entry to the API (and the WhatsApp webhook) | **Yes**: Cloudflare Tunnel | `cloudflared` on the server; no open ports. A stable hostname needs a domain on Cloudflare. |
| FastAPI API + worker | **No** | Cloudflare Containers need the Workers Paid plan ($5/month). Python Workers are free but run on Pyodide (WebAssembly) with no long-running process, so they can't host the IMAP poller or the job worker without a rewrite. |
| PostgreSQL | **No** | Cloudflare has D1 (SQLite) and Hyperdrive (a proxy to an external Postgres), not managed Postgres |

## Recommended: free

```
Browser ──▶ Cloudflare Pages (dashboard)
        └─▶ api.<domain> ──Cloudflare Tunnel──▶ Oracle Cloud Always Free VM
                                                  docker compose: api, worker, postgres
Meta webhook ─▶ api.<domain>/api/v1/webhooks/whatsapp ─┘
```

- **Compute:** an Oracle Cloud *Always Free* Ampere VM (up to 4 ARM cores and
  24 GB RAM) runs the existing `docker-compose.yml` unchanged. Signing up needs
  a card for identity verification, and ARM capacity is sometimes unavailable
  in a region; retry, or pick another home region.
- **Edge:** Cloudflare Pages for the dashboard; a named Cloudflare Tunnel
  exposes `api.<domain>` with HTTPS and no open ports. The same URL is the
  WhatsApp webhook.
- **Cost:** $0, plus a domain if you don't have one (about $10/year; it must
  use Cloudflare DNS for a named tunnel).

## Alternatives

- **Cloudflare-native ($5/month):** Cloudflare Containers run the api and worker
  images, with an external free Postgres (Neon free tier).
- **Demo from this machine ($0):** run the stack in WSL Docker with a Cloudflare
  Tunnel. It's only up while the PC is on; fine for a live interview demo.

## Development webhook

Meta needs a public HTTPS URL even in development. Use a quick tunnel (no
account or domain needed; the URL changes on every run):

```bash
cloudflared tunnel --url http://localhost:8000
```
