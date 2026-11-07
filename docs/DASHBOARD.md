# Dashboard

The web app sales reps and admins work in. The visual direction is in `docs/DESIGN.md`.

## Screens

| Screen | What it shows |
|--------|---------------|
| **Sign in** | Password, plus "Sign in with Microsoft" when Entra is configured. Problems from the Microsoft flow (`?sso_error=`) are explained in plain words. Either way you land back where you were headed, such as an AI app's consent page. |
| **Leads** (home) | Stats for the last 24 hours and 30 days, then "Needs you now". For a rep, that's their open high and critical leads; for an admin, it's the team's. Then the full table. Filters, search, sort and page are in the URL, so a view can be shared. Below 768 px the table becomes cards. |
| **Lead detail** | The conversation (auto-replies labelled, delivery status shown) and a reply or internal-note composer. The score card shows the factor breakdown, matched keywords, cap and the AI rationale. Status and owner controls, similar past leads (pgvector), a duplicate notice and the full history. |
| **Notifications** | A bell with the unread count, a panel listing alerts, and a toast for each new one, all live over SSE. A "Live" dot shows when the stream is connected. |
| **AI apps** | The MCP server URL to add to Claude or ChatGPT, what the tools can do, and the user's connected apps with "last used" and Disconnect (`docs/MCP.md`). |
| **Connect an AI app** | The consent page an AI app sends the browser to: who is signed in, what the app will be able to do, and where it returns to. Allow or Cancel. Outside the nav shell, like Sign in. |
| **Settings** (admin) | Scoring weights and thresholds, with a live preview of the last 20 leads rescored. Also keywords, auto-reply templates per language, escalation emails, mailboxes, users, and integrations. WhatsApp, Slack, Teams and Microsoft sign-in are set up right there: each has a form, a **Test** button, and the URLs to paste into Meta or Entra. Values saved there override `.env`, and removing one falls back to it. Secrets are encrypted with `SECRETS_ENCRYPTION_KEY` and never shown again. A change applies everywhere within about 15 seconds, without a restart. OpenAI and the encryption key stay server settings. |

Light and dark themes are both supported. Arabic content keeps its direction (`dir="auto"`) and uses IBM Plex Sans Arabic.

## Who can do what

| | Sales rep | Admin |
|---|---|---|
| See every lead, reply, add notes, change status | yes | yes |
| Claim an unassigned lead | yes | yes |
| Reassign or unassign any lead | – | yes |
| Settings, mailboxes, users | – | yes |

Moving a lead out of `new`, or replying, records its first response, which stops the SLA
escalation (`docs/NOTIFICATIONS.md`). A reply goes out on the lead's channel, from the
mailbox or number it came in on, threaded under the customer's last message. WhatsApp
replies are only possible within 24 hours of that message. Replies are sent by the
worker with the same at-most-once rules as auto-replies (`docs/AUTO_REPLY.md`), but
without the automatic-reply headers. Spam leads can't be replied to, because a reply confirms the address to the sender. A reply, or any first response, also stops the automatic acknowledgement from going out late.

## How it talks to the API

- **Typed client:** `pnpm gen:api` exports the backend's OpenAPI document and generates
  TanStack Query hooks with orval (`src/api/generated.ts`). There are no hand-written
  fetches for backend endpoints, except the SSE stream.
- **Tokens:** the access token lives only in memory. The refresh token is an httpOnly,
  SameSite=strict cookie scoped to `/api/v1/auth`. A reload restores the session from the
  cookie. On a 401 the client refreshes once and retries, and concurrent 401s share one
  refresh, because refresh tokens rotate. Across tabs, a Web Lock makes refreshes take turns,
  so two tabs can't send the same rotated cookie (`src/lib/api-client.ts`).
- **Live updates:** a fetch-based SSE reader (`src/lib/sse.ts`), because EventSource can't
  send a Bearer header. It reconnects with backoff, refreshes the token when the stream
  reports `expired`, and reloads the lists on `ready` and `resync`.

## Demo data

```bash
docker compose stop worker          # it would race the seed for the leads' jobs
cd backend
uv run python -m app.scripts.seed
uv run python -m app.scripts.seed_demo        # --reset to start over
docker compose start worker
```

`seed_demo` runs 17 realistic GCC enquiries (English and Arabic, email and WhatsApp, every
category) through the real ingestion, scoring, assignment and alerting code. Only the
OpenAI calls are replaced, by a scripted analysis and topic-based embeddings. Leads are
then spread over two weeks and moved along: contacted, qualified, converted, lost. It
refuses to run in production.
