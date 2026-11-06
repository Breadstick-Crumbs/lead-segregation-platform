from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import REFRESH_COOKIE
from app.models import RefreshToken
from tests.integration.conftest import TEST_PASSWORD, CreateUser, bearer, login

pytestmark = pytest.mark.integration


async def test_login_returns_access_token_and_sets_refresh_cookie(
    api: AsyncClient, create_user: CreateUser
) -> None:
    await create_user("layla@tawasol.test")

    response = await api.post(
        "/api/v1/auth/login", json={"email": "Layla@Tawasol.test", "password": TEST_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["user"]["email"] == "layla@tawasol.test"
    assert "password_hash" not in body["user"]
    cookie = response.headers["set-cookie"]
    assert f"{REFRESH_COOKIE}=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "SameSite=strict" in cookie
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("email", "password"),
    [("layla@tawasol.test", "wrong-password"), ("nobody@tawasol.test", TEST_PASSWORD)],
)
async def test_login_rejects_bad_credentials_with_the_same_message(
    api: AsyncClient, create_user: CreateUser, email: str, password: str
) -> None:
    await create_user("layla@tawasol.test")

    response = await api.post("/api/v1/auth/login", json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid email or password"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_login_rejects_inactive_user(api: AsyncClient, create_user: CreateUser) -> None:
    await create_user("gone@tawasol.test", is_active=False)

    response = await api.post(
        "/api/v1/auth/login", json={"email": "gone@tawasol.test", "password": TEST_PASSWORD}
    )

    assert response.status_code == 401


async def test_me_requires_a_valid_token(api: AsyncClient, create_user: CreateUser) -> None:
    await create_user("layla@tawasol.test")
    token = await login(api, "layla@tawasol.test")

    assert (await api.get("/api/v1/auth/me")).status_code == 401
    assert (await api.get("/api/v1/auth/me", headers=bearer("not-a-jwt"))).status_code == 401
    response = await api.get("/api/v1/auth/me", headers=bearer(token))
    assert response.status_code == 200
    assert response.json()["email"] == "layla@tawasol.test"


async def test_refresh_rotates_the_refresh_token(api: AsyncClient, create_user: CreateUser) -> None:
    await create_user("layla@tawasol.test")
    await login(api, "layla@tawasol.test")
    first_cookie = api.cookies[REFRESH_COOKIE]

    response = await api.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert api.cookies[REFRESH_COOKIE] != first_cookie


async def test_reusing_a_rotated_refresh_token_revokes_the_session(
    api: AsyncClient, create_user: CreateUser, db_session: AsyncSession
) -> None:
    await create_user("layla@tawasol.test")
    await login(api, "layla@tawasol.test")
    stolen = api.cookies[REFRESH_COOKIE]
    assert (await api.post("/api/v1/auth/refresh")).status_code == 200
    current = api.cookies[REFRESH_COOKIE]

    api.cookies.set(REFRESH_COOKIE, stolen, path="/api/v1/auth")
    assert (await api.post("/api/v1/auth/refresh")).status_code == 401

    # The legitimate, newer token is revoked too: the whole family is gone.
    api.cookies.set(REFRESH_COOKIE, current, path="/api/v1/auth")
    assert (await api.post("/api/v1/auth/refresh")).status_code == 401
    active = await db_session.scalar(
        select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None))
    )
    assert active == 0


async def test_refresh_without_cookie_is_rejected(api: AsyncClient) -> None:
    response = await api.post("/api/v1/auth/refresh")

    assert response.status_code == 401


async def test_logout_revokes_the_refresh_token(api: AsyncClient, create_user: CreateUser) -> None:
    await create_user("layla@tawasol.test")
    await login(api, "layla@tawasol.test")
    cookie = api.cookies[REFRESH_COOKIE]

    assert (await api.post("/api/v1/auth/logout")).status_code == 204

    api.cookies.set(REFRESH_COOKIE, cookie, path="/api/v1/auth")
    assert (await api.post("/api/v1/auth/refresh")).status_code == 401


async def test_deactivated_user_is_locked_out_immediately(
    api: AsyncClient, create_user: CreateUser, db_session: AsyncSession
) -> None:
    user = await create_user("layla@tawasol.test")
    token = await login(api, "layla@tawasol.test")

    user.is_active = False
    await db_session.flush()

    assert (await api.get("/api/v1/auth/me", headers=bearer(token))).status_code == 401


async def test_change_password(api: AsyncClient, create_user: CreateUser) -> None:
    await create_user("layla@tawasol.test")
    token = await login(api, "layla@tawasol.test")

    wrong = await api.post(
        "/api/v1/auth/change-password",
        headers=bearer(token),
        json={"current_password": "not-it", "new_password": "a-brand-new-passphrase"},
    )
    assert wrong.status_code == 401

    ok = await api.post(
        "/api/v1/auth/change-password",
        headers=bearer(token),
        json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-passphrase"},
    )
    assert ok.status_code == 204
    # This session survives; only the new password works for new logins.
    assert (await api.post("/api/v1/auth/refresh")).status_code == 200
    await login(api, "layla@tawasol.test", "a-brand-new-passphrase")
    bad = await api.post(
        "/api/v1/auth/login", json={"email": "layla@tawasol.test", "password": TEST_PASSWORD}
    )
    assert bad.status_code == 401


async def test_change_password_signs_out_other_sessions(
    api: AsyncClient, create_user: CreateUser
) -> None:
    await create_user("layla@tawasol.test")
    await login(api, "layla@tawasol.test")
    other_device = api.cookies[REFRESH_COOKIE]
    api.cookies.clear()
    token = await login(api, "layla@tawasol.test")

    await api.post(
        "/api/v1/auth/change-password",
        headers=bearer(token),
        json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-passphrase"},
    )

    api.cookies.set(REFRESH_COOKIE, other_device, path="/api/v1/auth")
    assert (await api.post("/api/v1/auth/refresh")).status_code == 401


async def test_refresh_is_refused_for_a_deactivated_user(
    api: AsyncClient, create_user: CreateUser, db_session: AsyncSession
) -> None:
    user = await create_user("layla@tawasol.test")
    await login(api, "layla@tawasol.test")

    user.is_active = False  # directly, bypassing the service that would also revoke tokens
    await db_session.flush()

    assert (await api.post("/api/v1/auth/refresh")).status_code == 401
    live = await db_session.scalar(
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
    )
    assert live == 0


async def test_expired_refresh_token_is_rejected(
    api: AsyncClient, create_user: CreateUser, db_session: AsyncSession
) -> None:
    await create_user("layla@tawasol.test")
    await login(api, "layla@tawasol.test")
    await db_session.execute(
        update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    response = await api.post("/api/v1/auth/refresh")

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Refresh token expired"
