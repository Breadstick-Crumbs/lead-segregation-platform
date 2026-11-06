"""Shared FastAPI dependencies: database session, settings and the authenticated user."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError, NotFoundError, PermissionDeniedError
from app.core.security import decode_access_token
from app.db.session import get_session
from app.integrations.microsoft_entra import EntraClient
from app.models import User
from app.models.enums import Role
from app.repositories.users import UserRepository

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_bearer = HTTPBearer(auto_error=False)


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


async def require_admin(user: CurrentUser) -> User:
    if user.role != Role.ADMIN:
        raise PermissionDeniedError("Admin access required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


@lru_cache
def _shared_entra_client() -> EntraClient:
    # One instance per process so its JWKS cache is shared across requests.
    return EntraClient.from_settings(get_settings())


def get_entra_client(settings: SettingsDep) -> EntraClient:
    if not settings.entra_enabled:
        raise NotFoundError("Sign in with Microsoft is not enabled")
    return _shared_entra_client()


EntraClientDep = Annotated[EntraClient, Depends(get_entra_client)]
