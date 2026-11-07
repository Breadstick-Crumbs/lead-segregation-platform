"""Every public endpoint in `RULES` is limited per client address, and sign-in is also
limited per account and network."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_effective_settings
from app.api.rate_limit import RULES, Rule
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.rate_limit import RateLimiter
from app.db.session import get_session
from app.main import create_app
from app.services.auth import AuthService, IssuedTokens
from tests.integration.conftest import TEST_PASSWORD, CreateUser

pytestmark = pytest.mark.integration

ClientAt = Callable[[str], AbstractAsyncContextManager[AsyncClient]]


@pytest.fixture
def app(db_session: AsyncSession) -> FastAPI:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client_at(app: FastAPI) -> ClientAt:
    """A client whose requests come from the given IP address."""

    @asynccontextmanager
    async def _client(ip: str) -> AsyncIterator[AsyncClient]:
        transport = ASGITransport(app=app, client=(ip, 40000))
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c

    return _client


def _limiter(app: FastAPI) -> RateLimiter:
    limiter: RateLimiter = app.state.rate_limiter
    return limiter


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.name)
async def test_each_public_endpoint_refuses_a_client_over_its_limit(
    app: FastAPI, client_at: ClientAt, rule: Rule
) -> None:
    for _ in range(rule.limit.requests):  # as if the client had used up its allowance
        _limiter(app).hit(f"{rule.name}:203.0.113.7", rule.limit)
    method = sorted(rule.methods)[0]

    async with client_at("203.0.113.7") as limited, client_at("198.51.100.1") as other:
        refused = await limited.request(method, rule.path)
        allowed = await other.request(method, rule.path)

    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["retry-after"]) <= rule.limit.window_seconds
    assert allowed.status_code != 429


async def test_sign_in_is_limited_per_address(client_at: ClientAt) -> None:
    async with client_at("203.0.113.7") as client:
        statuses = [
            (
                await client.post(
                    "/api/v1/auth/login",
                    json={"email": f"nobody{i}@tawasol.test", "password": "x"},
                )
            ).status_code
            for i in range(21)
        ]

    assert statuses == [401] * 20 + [429]


async def test_failed_sign_ins_lock_the_account_for_that_network_only(
    client_at: ClientAt, create_user: CreateUser
) -> None:
    await create_user("layla@tawasol.test")
    body = {"email": "layla@tawasol.test", "password": "wrong-password"}
    for i in range(10):  # spread over one /24, so the per-address limit never applies
        async with client_at(f"203.0.113.{i}") as client:
            assert (await client.post("/api/v1/auth/login", json=body)).status_code == 401

    async with client_at("203.0.113.200") as same_network:
        locked = await same_network.post(
            "/api/v1/auth/login", json={**body, "password": TEST_PASSWORD}
        )
        other_account = await same_network.post(
            "/api/v1/auth/login", json={"email": "someone@tawasol.test", "password": "x"}
        )
    # Someone failing on purpose elsewhere can't lock the real user out.
    async with client_at("198.51.100.1") as elsewhere:
        real_user = await elsewhere.post(
            "/api/v1/auth/login", json={**body, "password": TEST_PASSWORD}
        )

    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "rate_limited"
    assert int(locked.headers["retry-after"]) > 0
    assert other_account.status_code == 401
    assert real_user.status_code == 200


async def test_parallel_sign_in_attempts_cannot_overrun_the_account_limit(
    app: FastAPI, client_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the failure used to be counted after the (slow) password check, so a burst
    # of simultaneous attempts all passed the check.
    async def slow_failure(self: AuthService, email: str, password: str) -> IssuedTokens:
        await asyncio.sleep(0.05)
        raise AuthenticationError("Invalid email or password")

    monkeypatch.setattr(AuthService, "login", slow_failure)
    # The real settings dependency reads the database, which one test session can't share
    # between concurrent requests.
    app.dependency_overrides[get_effective_settings] = get_settings
    body = {"email": "layla@tawasol.test", "password": "guess"}

    async def attempt(i: int) -> int:
        async with client_at(f"203.0.113.{i}") as client:
            return (await client.post("/api/v1/auth/login", json=body)).status_code

    statuses = await asyncio.gather(*(attempt(i) for i in range(15)))

    assert sorted(statuses) == [401] * 10 + [429] * 5


async def test_successful_sign_ins_do_not_count_towards_the_account_limit(
    client_at: ClientAt, create_user: CreateUser
) -> None:
    await create_user("layla@tawasol.test")
    body = {"email": "layla@tawasol.test", "password": TEST_PASSWORD}
    for i in range(12):
        async with client_at(f"203.0.113.{i}") as client:
            assert (await client.post("/api/v1/auth/login", json=body)).status_code == 200


async def test_client_registration_is_limited_per_hour(client_at: ClientAt) -> None:
    async with client_at("203.0.113.7") as client:
        statuses = [
            (
                await client.post(
                    "/register",
                    json={
                        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                        "token_endpoint_auth_method": "none",
                    },
                )
            ).status_code
            for _ in range(11)
        ]

    assert statuses == [201] * 10 + [429]


async def test_head_requests_share_the_get_limit(app: FastAPI, client_at: ClientAt) -> None:
    rule = next(r for r in RULES if r.name == "oauth_authorize")
    for _ in range(rule.limit.requests):
        _limiter(app).hit("oauth_authorize:203.0.113.7", rule.limit)

    async with client_at("203.0.113.7") as client:
        assert (await client.head("/authorize")).status_code == 429


async def test_unlimited_endpoints_and_trailing_slashes(app: FastAPI, client_at: ClientAt) -> None:
    rule = next(r for r in RULES if r.name == "login")
    for _ in range(rule.limit.requests):
        _limiter(app).hit("login:203.0.113.7", rule.limit)

    async with client_at("203.0.113.7") as client:
        health = await client.get("/api/v1/health/live")
        slashed = await client.post("/api/v1/auth/login/", json={})

    assert health.status_code == 200
    assert slashed.status_code == 429
