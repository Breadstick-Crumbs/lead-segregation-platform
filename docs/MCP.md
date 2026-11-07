# MCP server

The sales team can work leads from Claude, ChatGPT or any client that speaks the Model
Context Protocol: "which critical WhatsApp leads are still unanswered?", "summarise the
Emirates Rail thread and draft a reply", "move it to qualified and assign it to Omar".

- **Endpoint:** `<FRONTEND_URL>/mcp`, using the Streamable HTTP transport, stateless, with JSON
  responses.
- **Code:** in `backend/app/mcp_server/`, built on the official Python SDK (`mcp` 2.x) and
  mounted inside the FastAPI app.
- **Permissions:** every call runs as the signed-in user, through the same services as the
  dashboard, so reps and admins keep exactly the permissions they have there
  (`docs/DASHBOARD.md`).

## Connecting

1. Open **AI apps** in the dashboard and copy the server URL.
2. Add it to your AI app:
   - **Claude:** as a custom connector.
   - **ChatGPT:** as an app, in developer mode.
   - **Claude Code:** with `claude mcp add --transport http tawasol <url>`.
3. The app opens a browser window to the dashboard's consent page. Sign in, with a password or
   Microsoft, check the app's name and where it will send you back to, and choose
   **Allow access**.
4. The app is connected. **AI apps** lists it, along with when it was last used, and
   **Disconnect** cuts it off on its next call.

Claude and ChatGPT on the web connect from their own servers, so they need the deployed
https address. Desktop clients and Claude Code can use a local one.

## Tools

| Tool | What it does | Kind |
|------|--------------|------|
| `search_leads` | Filter by text, status, priority, category, channel, owner and minimum score; sort by newest, score or activity | read |
| `leads_needing_attention` | Open high and critical leads, highest score first, with `sla_breached` marked. A rep sees their own by default, an admin the team's | read |
| `get_lead` | The conversation (latest 20 messages, each cut at 2,000 characters), notes, the score rationale, similar past leads, and whether a reply is possible | read |
| `lead_report` | The reporting figures for the last N days (`docs/REPORTING.md`) | read |
| `list_teammates` | Active users' ids, names and roles, for assigning. No emails: a rep sees colleagues as the dashboard shows them | read |
| `update_lead_status` | Change the status. Moving a lead out of `new` records the first response, as in the dashboard | change |
| `assign_lead` | `me`, `nobody`, or a teammate's id or full name. Reps can only claim unassigned leads | change |
| `add_note` | An internal note on the lead's timeline | change |
| `reply_to_lead` | Queue a reply on the lead's channel, with the same rules as a dashboard reply (threading, the 24-hour WhatsApp window, at-most-once sending) | sends to the customer |

The annotations tell clients which tools only read. `reply_to_lead` is marked destructive
and open-world, so clients ask before running it, and the server's instructions tell the
model to confirm the exact text with the user first.

Message text comes from customers, so it is untrusted. The tool descriptions and the
server instructions say to treat it as data, never as instructions. Results are structured
(Pydantic models), so clients get typed fields rather than prose.

## Sign-in: OAuth 2.1

The app is its own authorization server, as the MCP authorization spec describes. The SDK
handles the protocol and `app/services/oauth.py` holds the state in Postgres.

| Step | Endpoint |
|------|----------|
| Discovery | `/.well-known/oauth-protected-resource/mcp` (RFC 9728) names the issuer, and `/.well-known/oauth-authorization-server` (RFC 8414) lists the endpoints. A call to `/mcp` without a token gets a 401 whose `WWW-Authenticate` header points there |
| Registration | `/register`: dynamic client registration (RFC 7591), which is how Claude and ChatGPT register themselves |
| Authorization | `/authorize` checks the client, the redirect URI, PKCE (S256 only), the scope and the resource (RFC 8707), parks the request for 10 minutes, and sends the browser to `/oauth/consent` |
| Consent | The dashboard page asks the signed-in user. Allowing creates a grant and a one-time code (5 minutes) and redirects back to the client |
| Tokens | `/token` exchanges the code (with PKCE) and rotates refresh tokens |
| Revocation | `/revoke` (RFC 7009) |

### Guarantees

- **Tokens:** access tokens (60 minutes) and refresh tokens (30 days) are random strings,
  stored only as SHA-256 hashes. Both lifetimes are configurable (`MCP_ACCESS_TTL_MINUTES`,
  `MCP_REFRESH_TTL_DAYS`).
- **Checked on every call:** each MCP request checks the token, its grant and the user.
  Disconnecting the app, revoking a token or deactivating the user takes effect at once,
  not when the token expires.
- **Single use:**
  - A consent request is decided once.
  - A code can be exchanged once, and a second exchange revokes everything it bought
    (RFC 6749 §4.1.2). That holds even when two exchanges race: the loser finds the code
    used and revokes.
  - A refresh token can be used once. Each of these is one conditional UPDATE or DELETE,
    so two concurrent attempts can't both win.
  - A rotated refresh token that comes back more than 60 seconds later ends the whole
    connection, because someone holds a copy. One that comes back sooner is a client
    retrying after a timeout, or refreshing in parallel. It is refused, but the connection
    stays.
- **Reconnecting:** connecting the same app again replaces its earlier connection.
- **Bound to this server:** tokens are issued for this server only. A request naming another
  `resource` is refused with `invalid_target`.
- **Redirect URIs** must use https, or http to localhost for desktop clients, with no
  credentials or fragment. The consent page shows the real host, so
  `https://claude.ai@evil.example` can't pose as claude.ai. The approved one is stored on
  the grant, and AI apps shows it.
- **Registration limits:** at most 5 redirect URIs and 8 KB of metadata. A PKCE challenge
  must have the S256 shape.
- **Client secrets:** confidential clients get one, Fernet-encrypted at rest with
  `SECRETS_ENCRYPTION_KEY`. The token endpoint compares the secret, so it can't be a hash.
  Without the key, a client that asks for a secret gets an RFC 7591 error saying to register
  as a public client (`token_endpoint_auth_method: none`).
- **Errors:** a tool that fails unexpectedly returns a generic message and logs the details.
  Internal error text never reaches the AI client.
- **Housekeeping:** the nightly `prune_history` job deletes expired requests, codes and
  tokens. Grants are kept, revoked ones included, because they're small. Registered clients
  nobody ever approved are deleted after 30 days. A client that was approved once is kept,
  because Claude and ChatGPT reuse their saved `client_id` to reconnect.
- **Clickjacking:** the consent page can't be framed (`X-Frame-Options: DENY`,
  `frame-ancestors 'none'`).

## Deployment

The issuer, the MCP resource and the consent page share the dashboard's public origin
(`FRONTEND_URL`), so a single URL is enough. The reverse proxy in front of the frontend
must send these paths to the API (`PUBLIC_PATHS` in `app/mcp_server/server.py`):
- `/mcp`;
- `/.well-known/oauth-*`;
- `/authorize`, `/token`, `/register` and `/revoke`.

`frontend/nginx.conf` and `vite.config.ts` already do. `FRONTEND_URL` must be https except
on localhost: the SDK refuses to start with a plain-http public issuer.

**Transport security:** DNS-rebinding protection is off. It exists for unauthenticated
servers on localhost, while every call here carries a bearer token.

**Limits:** nginx caps request bodies on these paths at 64 KB. Each client address may
register 10 clients an hour and call `/authorize`, `/token` and `/revoke` 30 times a minute
(see `docs/SECURITY.md#rate-limiting`). Changing or resetting a password disconnects all of
that user's AI apps.

## Testing

`backend/tests/integration/test_mcp.py` covers the whole flow over the real HTTP endpoints:
- discovery;
- registration rules;
- consent, approve and deny;
- PKCE and the challenge's shape;
- code replay, including a lost race;
- refresh rotation, the retry window and late reuse;
- revocation, disconnecting and deactivation;
- expiry;
- the resource check;
- confidential clients;
- disguised redirect hosts;
- registering without an encryption key;
- pruning that keeps approved clients;
- every tool, including the rep and admin permission rules and no teammate emails;
- an unexpected error not leaking to the client.

The flow was also run with the SDK's own MCP client against the dev server:
1. The client discovered the server and registered itself.
2. Consent was approved in a browser.
3. The client exchanged the code and listed the tools.
4. `leads_needing_attention` and `lead_report` returned the demo data.
