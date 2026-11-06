import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.errors import AuthenticationError
from app.core.security import (
    JWT_ALGORITHM,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.enums import Role

SECRET = "unit-test-secret-that-is-long-enough"


def test_password_hash_round_trip() -> None:
    hashed = hash_password("correct-horse-battery")

    assert hashed.startswith("$argon2id$")
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_password_without_a_user_is_false() -> None:
    assert not verify_password("anything", None)


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, Role.ADMIN, secret=SECRET, ttl=timedelta(minutes=5))

    claims = decode_access_token(token, secret=SECRET)

    assert claims.user_id == user_id
    assert claims.role == Role.ADMIN


def test_expired_access_token_is_rejected() -> None:
    token = create_access_token(
        uuid.uuid4(),
        Role.SALES_REP,
        secret=SECRET,
        ttl=timedelta(minutes=5),
        now=datetime.now(UTC) - timedelta(hours=1),
    )

    with pytest.raises(AuthenticationError, match="expired"):
        decode_access_token(token, secret=SECRET)


def test_token_signed_with_another_secret_is_rejected() -> None:
    token = create_access_token(
        uuid.uuid4(),
        Role.ADMIN,
        secret="another-secret-entirely-xxxxxxxx",
        ttl=timedelta(minutes=5),
    )

    with pytest.raises(AuthenticationError):
        decode_access_token(token, secret=SECRET)


def test_non_access_token_type_is_rejected() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "admin",
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        SECRET,
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(AuthenticationError):
        decode_access_token(token, secret=SECRET)


def test_unsigned_token_is_rejected() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "admin",
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        key=None,  # type: ignore[arg-type]  # PyJWT needs key=None for alg "none"
        algorithm="none",
    )

    with pytest.raises(AuthenticationError):
        decode_access_token(token, secret=SECRET)


def test_refresh_tokens_are_random_and_hashed() -> None:
    first, second = generate_refresh_token(), generate_refresh_token()

    assert first != second
    assert len(hash_refresh_token(first)) == 64
    assert hash_refresh_token(first) == hash_refresh_token(first)
    assert first not in hash_refresh_token(first)
