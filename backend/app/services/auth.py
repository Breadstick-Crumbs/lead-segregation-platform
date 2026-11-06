"""Login, refresh-token rotation and logout."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password_async,
)
from app.models import RefreshToken, User
from app.repositories.users import RefreshTokenRepository, UserRepository

logger = get_logger(__name__)

INVALID_CREDENTIALS = "Invalid email or password"


@dataclass(frozen=True)
class IssuedTokens:
    user: User
    access_token: str
    access_expires_in: int  # seconds
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)
        self._tokens = RefreshTokenRepository(session)

    async def login(self, email: str, password: str) -> IssuedTokens:
        user = await self._users.get_by_email(email)
        password_ok = await verify_password_async(password, user.password_hash if user else None)
        if user is None or not password_ok:
            logger.info("login_failed", reason="bad_credentials")
            raise AuthenticationError(INVALID_CREDENTIALS)
        if not user.is_active:
            logger.info("login_failed", reason="inactive", user_id=str(user.id))
            raise AuthenticationError(INVALID_CREDENTIALS)

        now = datetime.now(UTC)
        user.last_login_at = now
        tokens = self._issue(user, family_id=uuid.uuid4(), now=now)
        await self._session.commit()
        logger.info("login_succeeded", user_id=str(user.id))
        return tokens

    async def refresh(self, raw_refresh_token: str) -> IssuedTokens:
        now = datetime.now(UTC)
        token_hash = hash_refresh_token(raw_refresh_token)
        claimed = await self._tokens.claim(token_hash, at=now)
        if claimed is None:
            await self._reject_unclaimable(token_hash, now=now)
        user_id, family_id = claimed

        user = await self._users.get(user_id)
        if user is None or not user.is_active:
            await self._tokens.revoke_family(family_id, at=now)
            await self._session.commit()
            raise AuthenticationError("Invalid refresh token")

        tokens = self._issue(user, family_id=family_id, now=now)
        await self._session.commit()
        return tokens

    async def _reject_unclaimable(self, token_hash: str, *, now: datetime) -> NoReturn:
        """Explain why a token couldn't be claimed, revoking the session if it was reused."""
        stored = await self._tokens.get_by_hash(token_hash)
        if stored is None:
            raise AuthenticationError("Invalid refresh token")
        if stored.revoked_at is not None:
            # A rotated token came back, so someone holds a copy: end the whole session.
            # Two tabs refreshing at once also land here; the client refreshes single-flight.
            await self._tokens.revoke_family(stored.family_id, at=now)
            await self._session.commit()
            logger.warning("refresh_token_reuse_detected", user_id=str(stored.user_id))
            raise AuthenticationError("Invalid refresh token")
        raise AuthenticationError("Refresh token expired")

    async def family_of(self, raw_refresh_token: str) -> uuid.UUID | None:
        stored = await self._tokens.get_by_hash(hash_refresh_token(raw_refresh_token))
        return stored.family_id if stored else None

    async def logout(self, raw_refresh_token: str) -> None:
        stored = await self._tokens.get_by_hash(hash_refresh_token(raw_refresh_token))
        if stored is not None:
            await self._tokens.revoke_family(stored.family_id, at=datetime.now(UTC))
            await self._session.commit()

    def _issue(self, user: User, *, family_id: uuid.UUID, now: datetime) -> IssuedTokens:
        access_ttl = timedelta(minutes=self._settings.jwt_access_ttl_minutes)
        refresh_expires_at = now + timedelta(days=self._settings.jwt_refresh_ttl_days)
        raw_refresh = generate_refresh_token()
        self._tokens.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_refresh_token(raw_refresh),
                family_id=family_id,
                expires_at=refresh_expires_at,
            )
        )
        access = create_access_token(
            user.id,
            user.role,
            secret=self._settings.jwt_secret.get_secret_value(),
            ttl=access_ttl,
            now=now,
        )
        return IssuedTokens(
            user=user,
            access_token=access,
            access_expires_in=int(access_ttl.total_seconds()),
            refresh_token=raw_refresh,
            refresh_expires_at=refresh_expires_at,
        )
