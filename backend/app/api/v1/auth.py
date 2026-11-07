import ipaddress
from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status

from app.api.deps import CurrentUser, RateLimiterDep, SessionDep, SettingsDep
from app.api.rate_limit import too_many_requests_message
from app.core.config import Settings
from app.core.errors import AuthenticationError, RateLimitedError
from app.core.rate_limit import Limit
from app.schemas.auth import AuthProviders, LoginRequest, TokenResponse
from app.schemas.user import PasswordChange, UserRead
from app.services.auth import AuthService, IssuedTokens
from app.services.users import UserService

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
# Scoped to the auth endpoints so the cookie isn't sent with every API call.
REFRESH_COOKIE_PATH = "/api/v1/auth"

# Failed sign-ins per account from one network (an IPv4 /24 or IPv6 /64): slows password
# guessing against one user, while someone elsewhere can't lock that user out by failing on
# purpose. See docs/SECURITY.md#rate-limiting.
ACCOUNT_LOGIN_FAILURES = Limit(requests=10, window_seconds=15 * 60)


def _network(client_ip: str) -> str:
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip
    prefix = 24 if isinstance(address, ipaddress.IPv4Address) else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def set_refresh_cookie(response: Response, tokens: IssuedTokens, settings: Settings) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        expires=tokens.refresh_expires_at,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


def _token_response(response: Response, tokens: IssuedTokens, settings: Settings) -> TokenResponse:
    set_refresh_cookie(response, tokens, settings)
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in=tokens.access_expires_in,
        user=UserRead.model_validate(tokens.user),
    )


@router.get("/providers")
async def auth_providers(settings: SettingsDep) -> AuthProviders:
    """Which sign-in methods the login screen should offer."""
    return AuthProviders(password=True, microsoft=settings.entra_enabled)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    *,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    # The email is already trimmed and lower-cased.
    key = f"login_account:{body.email}:{_network(client_ip)}"
    # The slot is taken before the password check (which awaits), so parallel attempts
    # can't all pass, and handed back if the sign-in succeeds: only failures count.
    retry_after = limiter.hit(key, ACCOUNT_LOGIN_FAILURES)
    if retry_after is not None:
        seconds, message = too_many_requests_message(retry_after)
        raise RateLimitedError(message, retry_after_seconds=seconds)
    tokens = await AuthService(session, settings).login(body.email, body.password)
    limiter.release(key)
    return _token_response(response, tokens, settings)


@router.post("/refresh")
async def refresh(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> TokenResponse:
    """Exchange the refresh cookie for a new access token; the refresh token rotates."""
    if not refresh_token:
        raise AuthenticationError("Missing refresh token")
    tokens = await AuthService(session, settings).refresh(refresh_token)
    return _token_response(response, tokens, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> None:
    if refresh_token:
        await AuthService(session, settings).logout(refresh_token)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


@router.get("/me")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> None:
    """Change your password. Other sessions are signed out; this one stays signed in."""
    current_family = (
        await AuthService(session, settings).family_of(refresh_token) if refresh_token else None
    )
    await UserService(session).change_own_password(user, body, keep_session_family=current_family)
