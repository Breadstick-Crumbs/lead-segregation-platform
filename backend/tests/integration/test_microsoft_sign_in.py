"""The full browser flow against a fake Entra: login redirect, callback, then refresh."""

import uuid
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_entra_client
from app.api.v1.auth import REFRESH_COOKIE
from app.api.v1.auth_microsoft import STATE_COOKIE, STATE_PURPOSE
from app.core.config import get_settings
from app.core.security import decode_short_lived
from app.db.session import get_session
from app.main import create_app
from tests.fakes.entra import FakeEntra
from tests.integration.conftest import CreateUser

pytestmark = pytest.mark.integration

FRONTEND = get_settings().frontend_url.rstrip("/")


@pytest.fixture
def entra() -> FakeEntra:
    return FakeEntra()


@pytest.fixture
async def browser(db_session: AsyncSession, entra: FakeEntra) -> AsyncIterator[AsyncClient]:
    app = create_app()
    client = entra.client()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_entra_client] = lambda: client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


async def _sign_in(browser: AsyncClient, entra: FakeEntra, *, code: str = "auth-code") -> str:
    """Run login + callback; returns where the callback redirected the browser."""
    start = await browser.get("/api/v1/auth/microsoft/login")
    assert start.status_code == 303
    query = {k: v[0] for k, v in parse_qs(urlparse(start.headers["location"]).query).items()}
    expected = decode_short_lived(
        browser.cookies[STATE_COOKIE],
        purpose=STATE_PURPOSE,
        secret=get_settings().jwt_secret.get_secret_value(),
    )
    # What Entra does on its side: remember the challenge, embed the nonce in the ID token.
    entra.challenges[code] = query["code_challenge"]
    entra.nonce = expected["nonce"]

    callback = await browser.get(
        "/api/v1/auth/microsoft/callback", params={"code": code, "state": query["state"]}
    )
    assert callback.status_code == 303
    location: str = callback.headers["location"]
    return location


async def test_invited_user_signs_in_and_gets_linked(
    browser: AsyncClient, entra: FakeEntra, create_user: CreateUser, db_session: AsyncSession
) -> None:
    user = await create_user("layla.hassan@tawasol.test", password=None)  # Microsoft-only

    assert await _sign_in(browser, entra) == f"{FRONTEND}/auth/callback"

    assert STATE_COOKIE not in browser.cookies
    refreshed = await browser.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()["user"]
    assert body["email"] == "layla.hassan@tawasol.test"
    assert body["microsoft_linked"] is True
    assert body["has_password"] is False
    await db_session.refresh(user)
    assert user.entra_oid == entra.object_id


async def test_linked_user_is_matched_by_object_id_after_an_email_change(
    browser: AsyncClient, entra: FakeEntra, create_user: CreateUser
) -> None:
    user = await create_user("layla.hassan@tawasol.test")
    user.entra_oid = entra.object_id
    entra.username = "layla.h@tawasol.test"  # renamed in Entra

    assert await _sign_in(browser, entra) == f"{FRONTEND}/auth/callback"


@pytest.mark.parametrize(
    ("setup", "reason"),
    [("none", "not_invited"), ("inactive", "inactive"), ("linked_elsewhere", "link_mismatch")],
)
async def test_sign_in_is_refused_without_a_matching_active_account(
    browser: AsyncClient, entra: FakeEntra, create_user: CreateUser, setup: str, reason: str
) -> None:
    if setup == "inactive":
        await create_user("layla.hassan@tawasol.test", is_active=False)
    elif setup == "linked_elsewhere":
        other = await create_user("layla.hassan@tawasol.test")
        other.entra_oid = uuid.uuid4()

    location = await _sign_in(browser, entra)

    assert location == f"{FRONTEND}/login?sso_error={reason}"
    assert REFRESH_COOKIE not in browser.cookies


async def test_callback_with_a_forged_state_is_refused(
    browser: AsyncClient, entra: FakeEntra, create_user: CreateUser
) -> None:
    await create_user("layla.hassan@tawasol.test")
    await browser.get("/api/v1/auth/microsoft/login")

    response = await browser.get(
        "/api/v1/auth/microsoft/callback", params={"code": "c", "state": "attacker-state"}
    )

    assert response.headers["location"] == f"{FRONTEND}/login?sso_error=failed"
    assert REFRESH_COOKIE not in browser.cookies


async def test_callback_without_the_state_cookie_is_refused(browser: AsyncClient) -> None:
    response = await browser.get(
        "/api/v1/auth/microsoft/callback", params={"code": "c", "state": "s"}
    )

    assert response.headers["location"] == f"{FRONTEND}/login?sso_error=failed"


async def test_user_cancelling_at_microsoft_is_reported(browser: AsyncClient) -> None:
    response = await browser.get(
        "/api/v1/auth/microsoft/callback",
        params={"error": "access_denied", "error_description": "The user cancelled"},
    )

    assert response.headers["location"] == f"{FRONTEND}/login?sso_error=cancelled"


async def test_microsoft_only_account_cannot_use_password_login(
    browser: AsyncClient, create_user: CreateUser
) -> None:
    await create_user("layla.hassan@tawasol.test", password=None)

    response = await browser.post(
        "/api/v1/auth/login", json={"email": "layla.hassan@tawasol.test", "password": "anything"}
    )

    assert response.status_code == 401


async def test_providers_and_login_when_sso_is_not_configured(api: AsyncClient) -> None:
    providers = await api.get("/api/v1/auth/providers")

    assert providers.json() == {"password": True, "microsoft": get_settings().entra_enabled}
    if not get_settings().entra_enabled:
        assert (await api.get("/api/v1/auth/microsoft/login")).status_code == 404
