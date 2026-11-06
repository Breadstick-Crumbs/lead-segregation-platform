"""Sign in with Microsoft: browser-facing redirects of the OIDC authorization-code flow.

Both endpoints are top-level browser navigations, so they answer with redirects rather than
JSON. Tokens never appear in a URL: success sets the refresh cookie and sends the browser to
the dashboard, which then calls POST /auth/refresh like any returning user.
"""

import secrets
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie
from fastapi.responses import RedirectResponse

from app.api.deps import EntraClientDep, SessionDep, SettingsDep
from app.api.v1.auth import set_refresh_cookie
from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.core.security import decode_short_lived, encode_short_lived
from app.integrations.microsoft_entra import EntraError, PkcePair
from app.services.sso import SsoService, SsoSignInError

router = APIRouter(prefix="/auth/microsoft", tags=["auth"])
logger = get_logger(__name__)

STATE_COOKIE = "entra_state"
STATE_COOKIE_PATH = "/api/v1/auth/microsoft"
STATE_PURPOSE = "entra_state"
STATE_TTL = timedelta(minutes=10)


def _failure(settings: Settings, reason: str) -> RedirectResponse:
    query = urlencode({"sso_error": reason})
    response = RedirectResponse(f"{settings.frontend_url.rstrip('/')}/login?{query}", 303)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return response


@router.get("/login", response_class=RedirectResponse, status_code=303)
async def microsoft_login(entra: EntraClientDep, settings: SettingsDep) -> RedirectResponse:
    """Start sign-in: redirect the browser to Microsoft."""
    state, nonce, pkce = secrets.token_urlsafe(32), secrets.token_urlsafe(32), PkcePair.generate()
    response = RedirectResponse(
        entra.authorization_url(state=state, nonce=nonce, code_challenge=pkce.challenge), 303
    )
    response.set_cookie(
        STATE_COOKIE,
        encode_short_lived(
            {"state": state, "nonce": nonce, "verifier": pkce.verifier},
            purpose=STATE_PURPOSE,
            secret=settings.jwt_secret.get_secret_value(),
            ttl=STATE_TTL,
        ),
        max_age=int(STATE_TTL.total_seconds()),
        path=STATE_COOKIE_PATH,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        # Lax, not Strict: Microsoft's redirect back here is a cross-site top-level navigation.
        samesite="lax",
    )
    return response


@router.get("/callback", response_class=RedirectResponse, status_code=303)
async def microsoft_callback(
    *,
    entra: EntraClientDep,
    session: SessionDep,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    state_cookie: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
) -> RedirectResponse:
    """Microsoft redirects here with an authorization code (or an error)."""
    if error:
        logger.info("sso_provider_error", error=error)
        return _failure(settings, "cancelled" if error == "access_denied" else "failed")
    if not (code and state and state_cookie):
        return _failure(settings, "failed")

    try:
        expected = decode_short_lived(
            state_cookie, purpose=STATE_PURPOSE, secret=settings.jwt_secret.get_secret_value()
        )
        if not secrets.compare_digest(expected.get("state", ""), state):
            raise AuthenticationError("State mismatch")
        id_token = await entra.exchange_code(code, code_verifier=expected["verifier"])
        identity = await entra.verify_id_token(id_token, nonce=expected["nonce"])
        tokens = await SsoService(session, settings).sign_in(identity)
    except SsoSignInError as exc:
        return _failure(settings, exc.reason)
    except (AuthenticationError, EntraError, KeyError) as exc:
        logger.warning("sso_failed", error=str(exc))
        return _failure(settings, "failed")

    response = RedirectResponse(f"{settings.frontend_url.rstrip('/')}/auth/callback", 303)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    set_refresh_cookie(response, tokens, settings)
    return response
