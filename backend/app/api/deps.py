"""Shared FastAPI dependencies: database session, settings and the authenticated user."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError, NotFoundError, PermissionDeniedError
from app.core.rate_limit import RateLimiter
from app.core.security import AccessTokenClaims, decode_access_token
from app.db.session import SessionFactory, get_session
from app.integrations.microsoft_entra import EntraClient
from app.models import User
from app.models.enums import Role
from app.repositories.users import UserRepository
from app.services import integrations

SessionDep = Annotated[AsyncSession, Depends(get_session)]
# The environment's settings alone, for the screen that edits the dashboard's overrides.
EnvSettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_effective_settings(session: SessionDep, base: EnvSettingsDep) -> Settings:
    """Settings as they apply now: the environment, with the integrations saved in the
    dashboard on top (`app.services.integrations`)."""
    return await integrations.effective(session, base)


SettingsDep = Annotated[Settings, Depends(get_effective_settings)]

_bearer = HTTPBearer(auto_error=False)


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise AuthenticationError("Not authenticated")
    claims = decode_access_token(
        credentials.credentials, secret=settings.jwt_secret.get_secret_value()
    )
    # Load the user on every request so deactivation and role changes apply immediately,
    # not when the access token expires.
    user = await UserRepository(session).get(claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_stream_claims(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AccessTokenClaims:
    """Like `CurrentUser`, for long-lived streams: the user is checked with a short session
    of its own, closed before the stream starts, so an open stream holds no pooled
    connection. The stream must end at `expires_at`."""
    if credentials is None:
        raise AuthenticationError("Not authenticated")
    claims = decode_access_token(
        credentials.credentials, secret=settings.jwt_secret.get_secret_value()
    )
    async with SessionFactory() as session:
        user = await UserRepository(session).get(claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Not authenticated")
    return claims


StreamClaims = Annotated[AccessTokenClaims, Depends(get_stream_claims)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != Role.ADMIN:
        raise PermissionDeniedError("Admin access required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


_entra_clients: dict[tuple[str, ...], EntraClient] = {}


def get_entra_client(settings: SettingsDep) -> EntraClient:
    if not settings.entra_enabled or settings.entra_client_secret is None:
        raise NotFoundError("Sign in with Microsoft is not enabled")
    # One instance per configuration, so its JWKS cache is shared across requests and a
    # change saved in the dashboard takes effect on the next sign-in.
    key = (
        str(settings.entra_tenant_id),
        str(settings.entra_client_id),
        settings.entra_client_secret.get_secret_value(),
        settings.entra_callback_url,
    )
    if key not in _entra_clients:
        _entra_clients.clear()
        _entra_clients[key] = EntraClient.from_settings(settings)
    return _entra_clients[key]


EntraClientDep = Annotated[EntraClient, Depends(get_entra_client)]
