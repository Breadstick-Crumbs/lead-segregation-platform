"""Password hashing, access tokens (JWT) and opaque refresh tokens."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
import jwt
from pwdlib import PasswordHash

from app.core.errors import AuthenticationError
from app.models.enums import Role

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"  # noqa: S105  (a claim value, not a credential)

_password_hash = PasswordHash.recommended()  # Argon2id
# Verified against when the email is unknown, so login takes the same time either way.
_DUMMY_HASH = _password_hash.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _password_hash.verify(password, _DUMMY_HASH)
        return False
    return _password_hash.verify(password, password_hash)


# Argon2 is deliberately slow and CPU-bound; in async code run it off the event loop so a
# burst of logins can't stall every other request (e.g. the WhatsApp webhook).
async def hash_password_async(password: str) -> str:
    return await anyio.to_thread.run_sync(hash_password, password)


async def verify_password_async(password: str, password_hash: str | None) -> bool:
    return await anyio.to_thread.run_sync(verify_password, password, password_hash)


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    role: Role
    expires_at: datetime


def create_access_token(
    user_id: uuid.UUID, role: Role, *, secret: str, ttl: timedelta, now: datetime | None = None
) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "type": ACCESS_TOKEN_TYPE,
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, *, secret: str) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat", "type"]},
        )
        if payload["type"] != ACCESS_TOKEN_TYPE:
            raise AuthenticationError("Invalid token type")
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            role=Role(payload["role"]),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token expired") from exc
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise AuthenticationError("Invalid access token") from exc


def encode_short_lived(
    payload: dict[str, str], *, purpose: str, secret: str, ttl: timedelta
) -> str:
    """Sign a small payload for a round trip through the browser (e.g. OIDC state)."""
    now = datetime.now(UTC)
    return jwt.encode(
        {**payload, "type": purpose, "iat": now, "exp": now + ttl}, secret, algorithm=JWT_ALGORITHM
    )


def decode_short_lived(token: str, *, purpose: str, secret: str) -> dict[str, str]:
    try:
        claims = jwt.decode(
            token, secret, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "type"]}
        )
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid or expired state") from exc
    if claims.get("type") != purpose:
        raise AuthenticationError("Invalid or expired state")
    return {k: v for k, v in claims.items() if isinstance(v, str)}


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    # Refresh tokens are 384 random bits, so a fast unsalted hash is enough to make a
    # leaked database row useless.
    return hashlib.sha256(token.encode()).hexdigest()
