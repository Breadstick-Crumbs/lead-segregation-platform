"""The MCP server end to end: discovery, dynamic registration, consent, tokens and the tools,
over the real HTTP endpoints an MCP client uses."""

import asyncio
import base64
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from mcp.server.auth.provider import TokenError
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Lead,
    LeadEvent,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthGrant,
    OAuthToken,
    User,
)
from app.models.enums import LeadEventType, LeadStatus, Priority, Role
from app.models.oauth import OAuthTokenKind
from app.schemas.mailbox import MailboxCreate
from app.services.mailboxes import MailboxService
from app.services.oauth import OAuthService
from app.services.reports import ReportService
from app.workers import names
from tests.integration.conftest import TEST_PASSWORD, CreateUser, bearer, login, queued_jobs
from tests.integration.test_leads_api import _lead
from tests.integration.test_mailboxes import NEW_MAILBOX

pytestmark = pytest.mark.integration

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
TOOLS = {
    "search_leads",
    "leads_needing_attention",
    "get_lead",
    "lead_report",
    "list_teammates",
    "update_lead_status",
    "assign_lead",
    "add_note",
    "reply_to_lead",
}


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={"secrets_encryption_key": SecretStr(Fernet.generate_key().decode())}
    )


@pytest.fixture
async def mcp(db_session: AsyncSession, settings: Settings) -> AsyncIterator[AsyncClient]:
    """The app at its public origin (FRONTEND_URL), with the MCP session manager running."""
    app = create_app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings
    # The session manager's task group must be entered and left in one task, and
    # pytest-asyncio tears fixtures down in another, so it runs in a task of its own.
    started, stop = asyncio.Event(), asyncio.Event()

    async def run_sessions() -> None:
        async with app.state.mcp_sessions.run():
            started.set()
            await stop.wait()

    runner = asyncio.create_task(run_sessions())
    await started.wait()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.frontend_url
        ) as client:
            yield client
    finally:
        stop.set()
        await runner


@pytest.fixture
async def team(create_user: CreateUser, db_session: AsyncSession) -> dict[str, User]:
    users = {
        "rep": await create_user("rep@tawasol.test", full_name="Rania Rep"),
        "other": await create_user("other@tawasol.test", full_name="Omar Other"),
        "admin": await create_user("boss@tawasol.test", role=Role.ADMIN, full_name="Bushra Boss"),
    }
    await db_session.commit()
    return users


def _resource(settings: Settings) -> str:
    return f"{settings.frontend_url.rstrip('/')}/mcp"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def _register(mcp: AsyncClient, **metadata: Any) -> dict[str, Any]:
    response = await mcp.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            **metadata,
        },
    )
    assert response.status_code == 201, response.text
    client: dict[str, Any] = response.json()
    return client


async def _authorize(
    mcp: AsyncClient, client_id: str, challenge: str, *, resource: str | None = None
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "the-state",
    }
    if resource is not None:
        params["resource"] = resource
    response = await mcp.get("/authorize", params=params)
    assert response.status_code == 302, response.text
    location: str = response.headers["location"]
    return location


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


async def _consent(mcp: AsyncClient, request_url: str, email: str, decision: str) -> str:
    assert urlsplit(request_url).path == "/oauth/consent"
    request_id = _query(request_url)["request"]
    headers = bearer(await login(mcp, email))
    shown = await mcp.get(f"/api/v1/oauth/requests/{request_id}", headers=headers)
    assert shown.status_code == 200, shown.text
    assert shown.json()["client_name"] == "Claude"
    assert shown.json()["redirect_host"] == "claude.ai"
    response = await mcp.post(f"/api/v1/oauth/requests/{request_id}/{decision}", headers=headers)
    assert response.status_code == 200, response.text
    redirect: str = response.json()["redirect_url"]
    return redirect


async def _exchange(
    mcp: AsyncClient, client_id: str, code: str, verifier: str, settings: Settings
) -> Any:
    return await mcp.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT,
            "resource": _resource(settings),
        },
    )


async def _connect(
    mcp: AsyncClient, settings: Settings, email: str = "rep@tawasol.test"
) -> dict[str, Any]:
    """The whole flow a client like Claude runs; returns its client id and tokens."""
    client = await _register(mcp)
    verifier, challenge = _pkce()
    consent_url = await _authorize(
        mcp, client["client_id"], challenge, resource=_resource(settings)
    )
    callback = _query(await _consent(mcp, consent_url, email, "approve"))
    assert callback["state"] == "the-state"
    response = await _exchange(mcp, client["client_id"], callback["code"], verifier, settings)
    assert response.status_code == 200, response.text
    return {"client_id": client["client_id"], **response.json()}


async def _rpc(
    mcp: AsyncClient, token: str, method: str, params: dict[str, Any] | None = None
) -> Any:
    return await mcp.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={**bearer(token), "Accept": "application/json, text/event-stream"},
    )


async def _tool(mcp: AsyncClient, token: str, name: str, **arguments: Any) -> dict[str, Any]:
    response = await _rpc(mcp, token, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def _error_text(result: dict[str, Any]) -> str:
    assert result["isError"] is True
    return " ".join(part["text"] for part in result["content"])


# Discovery and sign-in --------------------------------------------------------------


async def test_discovery_points_clients_at_the_authorization_server(
    mcp: AsyncClient, settings: Settings
) -> None:
    unauthenticated = await mcp.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert unauthenticated.status_code == 401
    challenge = unauthenticated.headers["www-authenticate"]
    assert "/.well-known/oauth-protected-resource/mcp" in challenge

    resource = (await mcp.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert resource["resource"] == _resource(settings)
    assert resource["scopes_supported"] == ["leads"]
    issuer = resource["authorization_servers"][0]

    metadata = (await mcp.get("/.well-known/oauth-authorization-server")).json()
    assert metadata["issuer"] == issuer
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["registration_endpoint"].endswith("/register")
    assert metadata["revocation_endpoint"].endswith("/revoke")


async def test_connect_and_use_the_tools(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    lead_id = await _lead(db_session, company_name="Desert Rose Trading")
    tokens = await _connect(mcp, settings)
    assert tokens["token_type"] == "Bearer"
    assert tokens["scope"] == "leads"

    listed = await _rpc(mcp, tokens["access_token"], "tools/list")
    assert listed.status_code == 200, listed.text
    tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    assert set(tools) == TOOLS
    assert tools["reply_to_lead"]["annotations"]["destructiveHint"] is True
    assert tools["search_leads"]["annotations"]["readOnlyHint"] is True

    found = await _tool(mcp, tokens["access_token"], "search_leads", text="desert")
    assert found["isError"] is False
    lead = found["structuredContent"]["leads"][0]
    assert lead["id"] == str(lead_id)
    assert lead["company"] == "Desert Rose Trading"
    assert lead["url"].endswith(f"/leads/{lead_id}")

    grant = await db_session.scalar(select(OAuthGrant))
    assert grant is not None
    assert grant.user_id == team["rep"].id
    assert grant.last_used_at is not None


async def test_declining_sends_access_denied_back(
    mcp: AsyncClient, settings: Settings, team: dict[str, User]
) -> None:
    client = await _register(mcp)
    _, challenge = _pkce()
    consent_url = await _authorize(mcp, client["client_id"], challenge)
    callback = _query(await _consent(mcp, consent_url, "rep@tawasol.test", "deny"))
    assert callback == {
        "error": "access_denied",
        "error_description": "The user declined access",
        "state": "the-state",
    }


async def test_a_consent_request_is_decided_once(mcp: AsyncClient, team: dict[str, User]) -> None:
    client = await _register(mcp)
    _, challenge = _pkce()
    consent_url = await _authorize(mcp, client["client_id"], challenge)
    await _consent(mcp, consent_url, "rep@tawasol.test", "approve")
    request_id = _query(consent_url)["request"]
    headers = bearer(await login(mcp, "rep@tawasol.test"))
    again = await mcp.post(f"/api/v1/oauth/requests/{request_id}/approve", headers=headers)
    assert again.status_code == 404
    assert "expired or was already used" in again.json()["error"]["message"]


async def test_expired_consent_request_is_refused(
    mcp: AsyncClient, db_session: AsyncSession, team: dict[str, User]
) -> None:
    client = await _register(mcp)
    _, challenge = _pkce()
    consent_url = await _authorize(mcp, client["client_id"], challenge)
    await db_session.execute(
        update(OAuthAuthorizationRequest).values(
            expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
    )
    headers = bearer(await login(mcp, "rep@tawasol.test"))
    request_id = _query(consent_url)["request"]
    assert (
        await mcp.get(f"/api/v1/oauth/requests/{request_id}", headers=headers)
    ).status_code == 404
    approve = await mcp.post(f"/api/v1/oauth/requests/{request_id}/approve", headers=headers)
    assert approve.status_code == 404


async def test_consent_needs_a_signed_in_user(mcp: AsyncClient) -> None:
    response = await mcp.post(f"/api/v1/oauth/requests/{uuid.uuid4()}/approve")
    assert response.status_code == 401


async def test_wrong_pkce_verifier_is_rejected(
    mcp: AsyncClient, settings: Settings, team: dict[str, User]
) -> None:
    client = await _register(mcp)
    _, challenge = _pkce()
    consent_url = await _authorize(mcp, client["client_id"], challenge)
    code = _query(await _consent(mcp, consent_url, "rep@tawasol.test", "approve"))["code"]
    wrong, _ = _pkce()
    response = await _exchange(mcp, client["client_id"], code, wrong, settings)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_replayed_code_revokes_what_it_bought(
    mcp: AsyncClient, settings: Settings, team: dict[str, User]
) -> None:
    client = await _register(mcp)
    verifier, challenge = _pkce()
    consent_url = await _authorize(mcp, client["client_id"], challenge)
    code = _query(await _consent(mcp, consent_url, "rep@tawasol.test", "approve"))["code"]
    first = await _exchange(mcp, client["client_id"], code, verifier, settings)
    assert first.status_code == 200
    access = first.json()["access_token"]
    assert (await _rpc(mcp, access, "tools/list")).status_code == 200

    replay = await _exchange(mcp, client["client_id"], code, verifier, settings)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert (await _rpc(mcp, access, "tools/list")).status_code == 401


async def test_refresh_rotates_and_late_reuse_ends_the_connection(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    tokens = await _connect(mcp, settings)

    def refresh(token: str) -> Any:
        return mcp.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": tokens["client_id"],
                "refresh_token": token,
            },
        )

    rotated = await refresh(tokens["refresh_token"])
    assert rotated.status_code == 200, rotated.text
    fresh = rotated.json()
    assert fresh["refresh_token"] != tokens["refresh_token"]
    assert (await _rpc(mcp, fresh["access_token"], "tools/list")).status_code == 200

    # A client retrying straight after the rotation (a timeout, a parallel refresh) is
    # refused, but its connection survives.
    retried = await refresh(tokens["refresh_token"])
    assert retried.status_code == 400
    assert retried.json()["error"] == "invalid_grant"
    assert (await _rpc(mcp, fresh["access_token"], "tools/list")).status_code == 200

    # Coming back later means someone holds a copy: everything from this connection stops.
    await db_session.execute(
        update(OAuthToken)
        .where(OAuthToken.revoked_at.is_not(None))
        .values(revoked_at=datetime.now(UTC) - timedelta(minutes=2))
    )
    reused = await refresh(tokens["refresh_token"])
    assert reused.status_code == 400
    assert (await _rpc(mcp, fresh["access_token"], "tools/list")).status_code == 401
    assert (await refresh(fresh["refresh_token"])).status_code == 400


async def test_a_code_lost_in_a_race_still_counts_as_a_replay(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    """Two exchanges that both loaded the code before either used it: the loser revokes."""
    client = await _register(mcp)
    _, challenge = _pkce()
    consent_url = await _authorize(mcp, client["client_id"], challenge)
    code = _query(await _consent(mcp, consent_url, "rep@tawasol.test", "approve"))["code"]
    service = OAuthService(db_session, settings)
    info = await service.get_client(client["client_id"])
    assert info is not None
    loaded = await service.load_code(info, code)
    assert loaded is not None
    first = await service.exchange_code(loaded)
    with pytest.raises(TokenError):
        await service.exchange_code(loaded)
    assert (await _rpc(mcp, first.access_token, "tools/list")).status_code == 401


async def test_disconnecting_an_app_cuts_it_off(
    mcp: AsyncClient, settings: Settings, team: dict[str, User]
) -> None:
    tokens = await _connect(mcp, settings)
    headers = bearer(await login(mcp, "rep@tawasol.test"))
    apps = (await mcp.get("/api/v1/oauth/connected-apps", headers=headers)).json()
    assert [(a["client_name"], a["redirect_host"]) for a in apps] == [("Claude", "claude.ai")]

    other = bearer(await login(mcp, "other@tawasol.test"))
    stranger = await mcp.delete(f"/api/v1/oauth/connected-apps/{apps[0]['id']}", headers=other)
    assert stranger.status_code == 404

    gone = await mcp.delete(f"/api/v1/oauth/connected-apps/{apps[0]['id']}", headers=headers)
    assert gone.status_code == 204
    assert (await _rpc(mcp, tokens["access_token"], "tools/list")).status_code == 401
    assert (await mcp.get("/api/v1/oauth/connected-apps", headers=headers)).json() == []


@pytest.mark.parametrize("how", ["admin_reset", "own_change"])
async def test_a_password_change_disconnects_ai_apps(
    mcp: AsyncClient, settings: Settings, team: dict[str, User], how: str
) -> None:
    # Regression: a stolen password could connect an MCP client that outlived the reset.
    tokens = await _connect(mcp, settings)
    if how == "admin_reset":
        admin = bearer(await login(mcp, "boss@tawasol.test"))
        changed = await mcp.patch(
            f"/api/v1/users/{team['rep'].id}", json={"password": "a-brand-new-pass"}, headers=admin
        )
    else:
        rep = bearer(await login(mcp, "rep@tawasol.test"))
        changed = await mcp.post(
            "/api/v1/auth/change-password",
            json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-pass"},
            headers=rep,
        )
    assert changed.status_code in (200, 204), changed.text

    assert (await _rpc(mcp, tokens["access_token"], "tools/list")).status_code == 401
    refreshed = await mcp.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": tokens["client_id"],
        },
    )
    assert refreshed.status_code == 400


async def test_reconnecting_replaces_the_old_connection(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    first = await _connect(mcp, settings)
    client_id = first["client_id"]
    verifier, challenge = _pkce()
    consent_url = await _authorize(mcp, client_id, challenge)
    code = _query(await _consent(mcp, consent_url, "rep@tawasol.test", "approve"))["code"]
    assert (await _exchange(mcp, client_id, code, verifier, settings)).status_code == 200

    assert (await _rpc(mcp, first["access_token"], "tools/list")).status_code == 401
    live = await db_session.scalar(
        select(func.count()).select_from(OAuthGrant).where(OAuthGrant.revoked_at.is_(None))
    )
    assert live == 1


async def test_revocation_endpoint_ends_the_connection(
    mcp: AsyncClient, settings: Settings, team: dict[str, User]
) -> None:
    tokens = await _connect(mcp, settings)
    response = await mcp.post(
        "/revoke",
        # The SDK's revocation form requires the field, empty for a public client.
        data={
            "client_id": tokens["client_id"],
            "client_secret": "",
            "token": tokens["refresh_token"],
        },
    )
    assert response.status_code == 200
    assert (await _rpc(mcp, tokens["access_token"], "tools/list")).status_code == 401


async def test_deactivated_user_loses_access_at_once(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    tokens = await _connect(mcp, settings)
    await db_session.execute(update(User).where(User.id == team["rep"].id).values(is_active=False))
    assert (await _rpc(mcp, tokens["access_token"], "tools/list")).status_code == 401


async def test_expired_access_token_is_refused(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    tokens = await _connect(mcp, settings)
    assert (await _rpc(mcp, tokens["access_token"], "tools/list")).status_code == 200
    await db_session.execute(
        update(OAuthToken)
        .where(OAuthToken.kind == OAuthTokenKind.ACCESS)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert (await _rpc(mcp, tokens["access_token"], "tools/list")).status_code == 401


async def test_registration_refuses_unsafe_redirects(mcp: AsyncClient) -> None:
    for uri in ("http://evil.example/callback", "javascript:alert(1)"):
        response = await mcp.post(
            "/register",
            json={
                "redirect_uris": [uri],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
        )
        assert response.status_code == 400, uri
    local = await mcp.post(
        "/register",
        json={
            "redirect_uris": ["http://127.0.0.1:33418/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
    )
    assert local.status_code == 201


async def test_redirect_hosts_cannot_be_disguised(mcp: AsyncClient, team: dict[str, User]) -> None:
    for uri in ("https://claude.ai@evil.example/cb", "https://claude.ai/cb#x"):
        response = await mcp.post(
            "/register",
            json={
                "redirect_uris": [uri],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
        )
        assert response.status_code == 400, uri
        assert response.json()["error"] == "invalid_redirect_uri"
    many = await mcp.post(
        "/register",
        json={
            "redirect_uris": [f"https://app.example/cb{i}" for i in range(6)],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
    )
    assert many.status_code == 400


async def test_a_malformed_pkce_challenge_is_refused(
    mcp: AsyncClient, team: dict[str, User]
) -> None:
    client = await _register(mcp)
    redirect = await _authorize(mcp, client["client_id"], "x" * 200)
    assert redirect.startswith(REDIRECT)
    assert _query(redirect)["error"] == "invalid_request"


async def test_secret_clients_get_a_clear_error_without_an_encryption_key(
    mcp: AsyncClient, settings: Settings
) -> None:
    app = mcp._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"secrets_encryption_key": None}
    )
    response = await mcp.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },  # no auth method: the SDK makes it client_secret_post
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"
    assert "'none'" in response.json()["error_description"]
    public = await _register(mcp)  # public clients still work
    assert public["client_id"]


async def test_a_token_for_another_resource_is_refused(
    mcp: AsyncClient, team: dict[str, User]
) -> None:
    client = await _register(mcp)
    _, challenge = _pkce()
    redirect = await _authorize(
        mcp, client["client_id"], challenge, resource="https://other.example/mcp"
    )
    assert redirect.startswith(REDIRECT)
    assert _query(redirect)["error"] == "invalid_target"


async def test_confidential_client_secret_is_stored_encrypted(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    client = await _register(mcp, token_endpoint_auth_method="client_secret_post")
    secret = client["client_secret"]
    stored = await db_session.get(OAuthClient, client["client_id"])
    assert stored is not None
    assert stored.secret_encrypted is not None
    assert secret not in stored.secret_encrypted
    assert "client_secret" not in stored.client_metadata

    verifier, challenge = _pkce()
    consent_url = await _authorize(mcp, client["client_id"], challenge)
    code = _query(await _consent(mcp, consent_url, "rep@tawasol.test", "approve"))["code"]
    form = {
        "grant_type": "authorization_code",
        "client_id": client["client_id"],
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT,
    }
    wrong = await mcp.post("/token", data={**form, "client_secret": "nope"})
    assert wrong.status_code == 401
    right = await mcp.post("/token", data={**form, "client_secret": secret})
    assert right.status_code == 200, right.text


async def test_prune_keeps_clients_that_were_ever_approved(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    connected = await _connect(mcp, settings)
    headers = bearer(await login(mcp, "rep@tawasol.test"))
    app_id = (await mcp.get("/api/v1/oauth/connected-apps", headers=headers)).json()[0]["id"]
    await mcp.delete(f"/api/v1/oauth/connected-apps/{app_id}", headers=headers)
    abandoned = await _register(mcp)
    long_ago = datetime.now(UTC) - timedelta(days=40)
    await db_session.execute(update(OAuthClient).values(created_at=long_ago))
    await db_session.execute(update(OAuthGrant).values(revoked_at=long_ago))

    counts = await OAuthService(db_session, settings).prune()
    assert counts["clients"] == 1
    assert await db_session.get(OAuthClient, abandoned["client_id"]) is None
    # The disconnected client is still known, so Claude can reconnect with its saved id.
    _, challenge = _pkce()
    consent_url = await _authorize(mcp, connected["client_id"], challenge)
    assert urlsplit(consent_url).path == "/oauth/consent"


# The tools --------------------------------------------------------------------------


async def test_tools_keep_dashboard_permissions(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    lead_id = await _lead(db_session)
    await db_session.execute(update(Lead).where(Lead.id == lead_id).values(owner_id=None))
    rep = (await _connect(mcp, settings, "rep@tawasol.test"))["access_token"]

    refused = await _tool(mcp, rep, "assign_lead", lead_id=str(lead_id), assignee="Omar Other")
    assert "Only admins can reassign" in _error_text(refused)

    claimed = await _tool(mcp, rep, "assign_lead", lead_id=str(lead_id), assignee="me")
    assert claimed["structuredContent"]["owner"] == {
        "id": str(team["rep"].id),
        "name": "Rania Rep",
    }

    admin = (await _connect(mcp, settings, "boss@tawasol.test"))["access_token"]
    moved = await _tool(mcp, admin, "assign_lead", lead_id=str(lead_id), assignee="omar other")
    assert moved["structuredContent"]["owner"]["name"] == "Omar Other"
    by_id = await _tool(
        mcp, admin, "assign_lead", lead_id=str(lead_id), assignee=str(team["rep"].id)
    )
    assert by_id["structuredContent"]["owner"]["name"] == "Rania Rep"
    unknown = await _tool(mcp, admin, "assign_lead", lead_id=str(lead_id), assignee="Nobody Here")
    assert "No active teammate" in _error_text(unknown)
    missing = await _tool(mcp, admin, "get_lead", lead_id=str(uuid.uuid4()))
    assert "Lead not found" in _error_text(missing)


async def test_status_change_is_recorded_as_the_user(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    lead_id = await _lead(db_session)
    token = (await _connect(mcp, settings))["access_token"]
    result = await _tool(mcp, token, "update_lead_status", lead_id=str(lead_id), status="qualified")
    assert result["structuredContent"]["status"] == "qualified"

    lead = await db_session.get(Lead, lead_id)
    assert lead is not None
    await db_session.refresh(lead)
    assert lead.first_response_at is not None
    event = await db_session.scalar(
        select(LeadEvent).where(
            LeadEvent.lead_id == lead_id, LeadEvent.type == LeadEventType.STATUS_CHANGED
        )
    )
    assert event is not None
    assert event.actor_id == team["rep"].id


async def test_get_lead_includes_notes_and_cuts_long_messages(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    lead_id = await _lead(db_session, body="We need 40 seats. " * 300)
    token = (await _connect(mcp, settings))["access_token"]
    noted = await _tool(mcp, token, "add_note", lead_id=str(lead_id), text="Called, keen")
    assert noted["structuredContent"]["author"] == "Rania Rep"

    details = (await _tool(mcp, token, "get_lead", lead_id=str(lead_id)))["structuredContent"]
    assert details["notes"][0]["text"] == "Called, keen"
    message = details["messages"][0]
    assert message["direction"] == "inbound"
    assert message["truncated"] is True
    assert len(message["text"]) == 2000
    assert details["can_reply"] is False  # no mailbox to send from
    assert "mailbox" in details["reply_blocked_reason"]


async def test_reply_is_queued_like_a_dashboard_reply(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    mailbox = await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )
    await db_session.commit()
    lead_id = await _lead(db_session, mailbox_id=mailbox.id)
    token = (await _connect(mcp, settings))["access_token"]
    result = await _tool(
        mcp, token, "reply_to_lead", lead_id=str(lead_id), text="Happy to help, Omar."
    )
    queued = result["structuredContent"]
    assert queued["delivery_status"] == "queued"
    assert queued["lead"]["status"] == LeadStatus.CONTACTED
    jobs = await queued_jobs(db_session, names.SEND_MESSAGE)
    assert [job["args"]["message_id"] for job in jobs] == [queued["message_id"]]


async def test_needing_attention_defaults_to_the_reps_own_leads(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    rep_id, other_id = team["rep"].id, team["other"].id
    mine = await _lead(db_session, intent=100, urgency=100, business_value=100)
    theirs = await _lead(db_session, intent=100, urgency=100, business_value=100)
    for lead_id, owner_id in ((mine, rep_id), (theirs, other_id)):
        await db_session.execute(update(Lead).where(Lead.id == lead_id).values(owner_id=owner_id))
    rep = (await _connect(mcp, settings))["access_token"]

    own = (await _tool(mcp, rep, "leads_needing_attention"))["structuredContent"]
    assert [lead["id"] for lead in own["leads"]] == [str(mine)]
    assert own["leads"][0]["priority"] in (Priority.CRITICAL, Priority.HIGH)
    everyone = (await _tool(mcp, rep, "leads_needing_attention", whose="everyone"))[
        "structuredContent"
    ]
    assert {lead["id"] for lead in everyone["leads"]} == {str(mine), str(theirs)}


async def test_report_and_teammates(
    mcp: AsyncClient, db_session: AsyncSession, settings: Settings, team: dict[str, User]
) -> None:
    await _lead(db_session)
    token = (await _connect(mcp, settings))["access_token"]
    report = (await _tool(mcp, token, "lead_report", days=7))["structuredContent"]
    assert report["total"] == 1
    assert len(report["volume"]) == 7
    people = (await _tool(mcp, token, "list_teammates"))["structuredContent"]["result"]
    assert {p["name"] for p in people} == {"Rania Rep", "Omar Other", "Bushra Boss"}
    # As in the dashboard, a rep sees colleagues' names and roles, never their emails.
    assert all(set(p) == {"id", "name", "role"} for p in people)


async def test_an_unexpected_error_does_not_leak_to_the_client(
    mcp: AsyncClient, settings: Settings, team: dict[str, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = (await _connect(mcp, settings))["access_token"]

    async def broken(*_: object, **__: object) -> None:
        raise RuntimeError("SELECT secret FROM internals")

    monkeypatch.setattr(ReportService, "report", broken)
    result = await _tool(mcp, token, "lead_report")
    text = _error_text(result)
    assert "Something went wrong" in text
    assert "internals" not in text
