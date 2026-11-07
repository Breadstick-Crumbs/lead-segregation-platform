# Security

The Phase 9 review covered the whole app: authentication, cookies, CORS, the HTTP headers
in `frontend/nginx.conf`, secrets, logging and error reporting. This page lists the
controls in place, what the review found and fixed, and the risks accepted for now.

## Controls

| Area | How it is handled |
|------|-------------------|
| Passwords | Argon2 (pwdlib). Unknown emails are checked against a dummy hash, so a sign-in takes the same time whether or not the account exists, and inactive accounts get the same message. |
| Access tokens | HS256 JWTs signed with `JWT_SECRET` (at least 32 characters in production), with `type`, `exp` and `iat` required and a 15-minute lifetime. They live only in browser memory. The user is reloaded on every request, so deactivation and role changes apply at once. |
| Refresh tokens | 384-bit random values, stored as SHA-256 hashes and rotated on every use. Reusing an old one revokes the whole session family. The cookie is `HttpOnly`, `SameSite=Strict` and `Secure` in production, and is scoped to `/api/v1/auth`. |
| Microsoft sign-in | Authorization code flow with PKCE (S256). `state` sits in a signed 10-minute cookie and is compared in constant time. The nonce, issuer, audience, tenant and signature are all checked against Entra's JWKS. There is no self-signup; see [MICROSOFT_SIGN_IN.md](MICROSOFT_SIGN_IN.md). |
| MCP OAuth | PKCE is required. Codes, refresh tokens and consent requests are single-use. Redirect URIs must be https or loopback, client secrets are encrypted, and every call reloads the grant and the user. See [MCP.md](MCP.md). |
| Roles | Admin-only routers (`AdminUser`) cover users, mailboxes, settings and integrations. Reps can only claim unassigned leads. MCP tools go through the same services. |
| CORS | Only the origins in `CORS_ORIGINS`. In production the dashboard and API share one origin, so the browser never makes a cross-origin call. |
| Webhooks | WhatsApp deliveries are checked with HMAC-SHA256 over the raw body. The endpoint refuses everything when the app secret is missing, and bodies are capped at 3 MB. The verify token is compared in constant time. |
| Secrets at rest | Mailbox passwords, dashboard-saved integration secrets and MCP client secrets are encrypted with Fernet (`SECRETS_ENCRYPTION_KEY`). The settings API never returns a secret, only whether one is set and where it comes from. |
| Outbound URLs | Slack webhooks must be on `hooks.slack.com`. Teams webhooks must be on `*.logic.azure.com`, `*.powerplatform.com` or `*.webhook.office.com`, so an admin session can't point the server at an internal address (SSRF). httpx doesn't follow redirects. |
| Untrusted content | Lead text is sent to the model inside delimiters, and the model's output is schema-validated. React escapes everything it renders; the code has no `dangerouslySetInnerHTML` and no dynamic `href`. |
| Errors | Only domain errors (`AppError`) return their message. Anything else returns a generic 500. AI failures are stored as a short category (for example "OpenAI returned HTTP 401"), never the provider's text. |
| Logging | structlog key-value fields: IDs, never tokens, codes, passwords or message bodies. JSON in production for the API, the worker and uvicorn's own lines. `X-Request-ID` is accepted only if it is 1–64 characters of `[A-Za-z0-9._-]`. |
| Production surface | `/docs`, `/redoc` and `/openapi.json` are off. Postgres and the worker publish no ports, and the API is reachable only through Caddy. The containers run as non-root users with read-only filesystems, no Linux capabilities and `no-new-privileges`. |

## HTTP headers

nginx (`frontend/security-headers.conf`) sends these on every response, and Caddy adds HSTS:

- **`Content-Security-Policy`:** `default-src 'self'; script-src 'self'`. Styles and fonts
  are allowed from Google Fonts. `connect-src` allows `'self'` plus Sentry's ingest hosts.
  Also set: `frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'`.
  The token in memory can only be sent to this origin. `style-src` keeps `'unsafe-inline'`
  for the inline styles that React and Recharts set.
- **Other headers:** `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, a `Permissions-Policy` that turns off
  the camera, microphone, geolocation and payment, and
  `Cross-Origin-Opener-Policy: same-origin`.
- **HSTS (Caddy):** `Strict-Transport-Security: max-age=31536000; includeSubDomains`.

## Rate limiting

`app/api/rate_limit.py` is pure ASGI middleware that runs before routing, so a refused
request never opens a database session. Refused requests get a `429` with `Retry-After`.
Limits are per client IP:

| Endpoint | Limit |
|----------|-------|
| `POST /api/v1/auth/login` | 20 / minute |
| `POST /api/v1/auth/refresh` | 60 / minute |
| `GET /api/v1/auth/microsoft/login`, `/callback` | 20 / minute each |
| `POST /register` (MCP client registration) | 10 / hour |
| `GET, POST /authorize` | 30 / minute |
| `POST /token`, `POST /revoke` | 30 / minute |
| `GET /api/v1/webhooks/whatsapp` (verify token) | 10 / minute |
| `POST /api/v1/webhooks/whatsapp` | 600 / minute (a flood ceiling; Meta retries on 429) |

Sign-in is also limited **per account and network**. After 10 failed attempts in 15 minutes
on one email from one IPv4 /24 (or IPv6 /64), that email gets `429` from that network until
the window passes.

- The slot is taken before the password is checked, so a burst of simultaneous attempts
  can't all pass, and it is handed back when the sign-in succeeds. Only failures count.
- Keying on the network means someone failing on purpose from elsewhere can't lock the
  real user out. The trade-off: an attacker with many networks gets 10 guesses per network
  per 15 minutes, still capped at 20 attempts a minute per address. Argon2 and the
  password policy make that a slow way in.
- A user locked out on their own network can still sign in with Microsoft.

**Why in-process rather than Postgres-backed:** the deployment runs one API container with
one uvicorn process, so a counter in memory sees every request. It costs no database round
trip on the endpoints being flooded, and it needs no table, migration or cleanup job. The
trade-offs:

- Counters reset when the API restarts, which gives an attacker one fresh window per deploy.
- Running more than one API replica would multiply each limit by the replica count.

If the API is ever scaled out, swap `RateLimiter` for a Postgres-backed one with the same
`hit`/`release`/`retry_after` interface (an `UNLOGGED` table keyed by rule and IP), or move the limit
to Caddy. The table holds at most 50,000 keys and evicts the least recently used.

**Client IP:** uvicorn's proxy-headers middleware reads `X-Forwarded-For` only from proxies
on the private Docker ranges (`FORWARDED_ALLOW_IPS` in `backend/Dockerfile`), and takes the
last address outside them. Caddy overwrites any `X-Forwarded-For` that a client sends, so a
client can't choose its own IP.

The trusted ranges are all of the private ranges, because the Docker networks' subnets
aren't known in advance. That is only safe when nothing on a private address can reach the
API without passing through Caddy, which is true in production. In the dev stack (API
published on :8000, or nginx reached from a LAN), a client can claim any address in
`X-Forwarded-For` and get past the per-IP limits. Running locally, that's acceptable. If
Docker ever presents Caddy's clients through its userland proxy (for example IPv6 without
ip6tables), every client would share one address, and its limits.

## Error reporting (Sentry)

Sentry is off unless `SENTRY_DSN` (API and worker) or `VITE_SENTRY_DSN` (browser, set at
build time) is set. Without a DSN, the backend never initialises the SDK and the browser
never even downloads it (it is a separate chunk).

When a DSN is set, these are reported:

- **API:** unhandled exceptions.
- **Worker:** jobs that crash.
- **Backend logs:** every `logger.error` and `logger.exception`, including the failures the
  app catches and handles. These go through a structlog processor, with the log's fields as
  extra data.
- **Browser:** errors React catches or can't handle, uncaught errors and unhandled promise
  rejections.

The `error` and `detail` fields of a log line are not sent, because a provider's error
text can quote a phone number back. The exception message itself is still sent, and an
exception raised while validating lead data (a pydantic `ValidationError`, for example)
can quote that data. This is a known residual risk: turn on Sentry's server-side data
scrubbing for the project as well.

Events are tagged with `component` (`api` or `worker`), the environment and `APP_RELEASE`
(the commit, in CI-built images). Nothing personal leaves the process. The backend collects
no request bodies, cookies, query strings, user details or stack-frame variables. The
browser collects no user details, cookies, query strings or bodies. A test fails if a
password, message text, bearer token or cookie reaches an event
(`backend/tests/unit/test_observability.py`).

## Review findings (Phase 9)

| # | Severity | Finding | Outcome |
|---|----------|---------|---------|
| 1 | Medium | A password reset didn't disconnect MCP clients. Someone who had stolen a password could connect their own AI app and keep access for 30 days after the reset. | **Fixed.** Admin resets and self-service changes now revoke every OAuth grant. Regression test in `test_mcp.py`. |
| 2 | Medium | The SPA had no CSP beyond `frame-ancestors`, and no `nosniff`, referrer policy or HSTS. | **Fixed.** See the HTTP headers section above. |
| 3 | Low | The WhatsApp verify token was compared with `!=`. | **Fixed.** It now uses `hmac.compare_digest`. |
| 4 | Low | The Teams webhook URL accepted any https host, a blind SSRF from an admin session. | **Fixed.** The host must be on an allowlist, with tests. |
| 5 | Low | OpenAI's raw error text, which can include request IDs, the organisation and a masked key, was stored on the lead and shown to every rep. | **Fixed.** Leads store a short category instead, and the full text goes to the log. Regression test in `test_openai_llm.py`. |
| 6 | Low | `/redoc` and `/openapi.json` stayed on in production. | **Fixed.** Both are off. |
| 7 | Low | Microsoft sign-in links an unlinked account by `preferred_username`. For a B2B guest in the tenant, that is an address the guest controls. | **Accepted, mitigated.** The attack only works against an app user who has never signed in with Microsoft and whose email isn't a UPN in the tenant. Mitigation: restrict guest invitations in Entra, or have each user sign in with Microsoft once (after that the account is bound to their Entra object ID). |
| – | Hardening | `X-Request-ID` was taken from the client unvalidated. | **Fixed.** It is validated and capped. |
| – | Hardening | Sentry would have collected stack-frame variables. | **Fixed.** They are off. |
| – | Hardening | The worker never configured logging, so its production logs weren't JSON, and uvicorn's lines bypassed the JSON formatter. | **Fixed.** The worker uses `app.workers.worker` and the API uses `uvicorn-log.json`. |
| – | Accepted | Dashboard sessions slide: each refresh extends the 14-day window, with no absolute maximum. | Accepted for now. Password changes, resets and deactivation end every session. |

## Reporting a vulnerability

Email the maintainer rather than opening a public issue.
