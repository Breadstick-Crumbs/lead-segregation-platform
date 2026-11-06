"""Races need real, separately committed transactions, so these tests don't use the shared
rolled-back session: each request gets its own session and the test cleans up after itself."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.v1.auth import REFRESH_COOKIE
from app.core.security import hash_password
from app.db.session import get_session
from app.main import create_app
from app.models import RefreshToken, User
from app.models.enums import Role
from tests.integration.conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration

EMAIL = "race@tawasol.test"


@pytest.fixture
async def isolated_api(engine: AsyncEngine) -> AsyncIterator[tuple[AsyncClient, AsyncEngine]]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            User(
                email=EMAIL,
                full_name="Race Condition",
                role=Role.SALES_REP,
                password_hash=hash_password(TEST_PASSWORD),
            )
        )
        await session.commit()

    app = create_app()

    async def _own_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = _own_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            yield c, engine
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.email == EMAIL))  # cascades tokens
            await session.commit()


async def test_concurrent_refreshes_with_one_token_cannot_both_succeed(
    isolated_api: tuple[AsyncClient, AsyncEngine],
) -> None:
    api, engine = isolated_api
    login = await api.post("/api/v1/auth/login", json={"email": EMAIL, "password": TEST_PASSWORD})
    assert login.status_code == 200
    token = api.cookies[REFRESH_COOKIE]
    api.cookies.clear()

    async def _refresh() -> int:
        # Sent as a header so concurrent responses' Set-Cookie can't swap the token mid-race.
        response = await api.post(
            "/api/v1/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={token}"}
        )
        return response.status_code

    statuses = await asyncio.gather(*(_refresh() for _ in range(5)))

    assert statuses.count(200) <= 1
    async with AsyncSession(engine) as session:
        live = await session.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .join(User, User.id == RefreshToken.user_id)
            .where(User.email == EMAIL, RefreshToken.revoked_at.is_(None))
        )
    # Either one rotation won and the losers were treated as reuse (revoking it), or nothing
    # is live: never two valid tokens in one session.
    assert live is not None
    assert live <= 1
