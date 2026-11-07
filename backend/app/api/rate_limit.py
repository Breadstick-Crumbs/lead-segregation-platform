"""Per-client-IP limits on the public, unauthenticated endpoints: sign-in, token refresh,
the MCP OAuth server and the WhatsApp webhook. Pure ASGI, so it also covers the mounted
MCP app, and a rejected request never reaches a database session.

The client IP is `scope["client"]`, which uvicorn's proxy-headers middleware rewrites from
`X-Forwarded-For` for trusted proxies only (`--forwarded-allow-ips`, see the production
Dockerfile). Without that, every request behind the proxy shares the proxy's address."""

import json
import math
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.logging import get_logger
from app.core.rate_limit import Limit, RateLimiter

logger = get_logger(__name__)


@dataclass(frozen=True)
class Rule:
    name: str
    methods: frozenset[str]
    path: str
    limit: Limit


def _rule(name: str, methods: str, path: str, requests: int, per_seconds: float) -> Rule:
    return Rule(name, frozenset(methods.split(",")), path, Limit(requests, per_seconds))


MINUTE = 60.0
HOUR = 3600.0

RULES = (
    # Brute force across accounts from one address, with room for an office behind one NAT.
    # Per-account limits are in `auth.login`.
    _rule("login", "POST", "/api/v1/auth/login", 20, MINUTE),
    # The dashboard refreshes on every page load and every 15 minutes per tab. Refresh tokens
    # can't be guessed, so this only stops floods.
    _rule("refresh", "POST", "/api/v1/auth/refresh", 60, MINUTE),
    _rule("microsoft_login", "GET", "/api/v1/auth/microsoft/login", 20, MINUTE),
    _rule("microsoft_callback", "GET", "/api/v1/auth/microsoft/callback", 20, MINUTE),
    # Anonymous dynamic client registration writes a row per call: keep it scarce.
    _rule("oauth_register", "POST", "/register", 10, HOUR),
    _rule("oauth_authorize", "GET,POST", "/authorize", 30, MINUTE),
    _rule("oauth_token", "POST", "/token", 30, MINUTE),
    _rule("oauth_revoke", "POST", "/revoke", 30, MINUTE),
    # Guessing the verify token. Meta calls this once, when the webhook is subscribed.
    _rule("whatsapp_verify", "GET", "/api/v1/webhooks/whatsapp", 10, MINUTE),
    # A flood ceiling only: Meta delivers from a small pool of addresses, and a 429 makes it
    # retry with backoff rather than drop the event.
    _rule("whatsapp_webhook", "POST", "/api/v1/webhooks/whatsapp", 600, MINUTE),
)


def _match(method: str, path: str) -> Rule | None:
    if method == "HEAD":  # served by the GET handler, so it shares GET's limit
        method = "GET"
    normalized = path.rstrip("/") or "/"
    for rule in RULES:
        if rule.path == normalized and method in rule.methods:
            return rule
    return None


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, limiter: RateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rule = _match(scope["method"], scope["path"])
        if rule is None:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        retry_after = self.limiter.hit(f"{rule.name}:{ip}", rule.limit)
        if retry_after is None:
            await self.app(scope, receive, send)
            return
        logger.warning("rate_limited", rule=rule.name, client_ip=ip)
        await send_too_many_requests(send, retry_after)


def too_many_requests_message(retry_after: float) -> tuple[int, str]:
    seconds = max(math.ceil(retry_after), 1)
    return seconds, f"Too many attempts. Try again in {seconds} seconds."


async def send_too_many_requests(send: Send, retry_after: float) -> None:
    seconds, message = too_many_requests_message(retry_after)
    body = json.dumps({"error": {"code": "rate_limited", "message": message}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", str(seconds).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
