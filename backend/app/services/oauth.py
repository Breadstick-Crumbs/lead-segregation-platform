"""The OAuth 2.1 authorization server behind the MCP endpoint.

The MCP SDK handles the protocol (metadata, /authorize, /token, /register, /revoke,
PKCE checks); this service holds the state. The user signs in and approves on the
dashboard's consent page, so password and Microsoft sign-in both work."""

import hashlib
import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as TokenResponse
from pydantic import AnyUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import SecretBox, SecretsNotConfiguredError
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.models import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthGrant,
    OAuthToken,
    User,
)
from app.models.oauth import OAuthTokenKind
from app.repositories.oauth import OAuthRepository
from app.schemas.oauth import ConnectedAppRead, ConsentRequestRead

logger = get_logger(__name__)

SCOPE = "leads"
REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)
TOUCH_EVERY = timedelta(minutes=5)  # how stale "last used" may get, to spare a write per call
PRUNE_MARGIN = timedelta(days=1)
# A registration nobody ever approved is dropped after this long. Clients that were approved
# once are kept: Claude and ChatGPT remember their client_id and reuse it to reconnect.
UNUSED_CLIENT_TTL = timedelta(days=30)
# A rotated refresh token presented again this soon is a client retry or a parallel refresh
# (a timeout after the server rotated), not theft: it's refused without ending the connection.
REFRESH_REUSE_GRACE = timedelta(seconds=60)
MAX_REDIRECT_URIS = 5
MAX_METADATA_BYTES = 8_000
S256_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")  # base64url of a SHA-256 digest, unpadded
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
EXPIRED_REQUEST = (
    "This sign-in request has expired or was already used. Start again from your AI app."
)


def hash_secret(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _timestamp(at: datetime) -> int:
    return int(at.timestamp())


def _host(uri: str) -> str:
    """The host the browser really goes to, as the consent page shows it. Userinfo is refused
    at registration, so `https://claude.ai@evil.example` can't pose as claude.ai."""
    parts = urlsplit(uri)
    host = parts.hostname or uri
    return f"{host}:{parts.port}" if parts.port else host


def mcp_resource_url(settings: Settings) -> str:
    return f"{settings.frontend_url.rstrip('/')}/mcp"


def _same_resource(requested: str, ours: str) -> bool:
    return requested.rstrip("/") == ours.rstrip("/")


class OAuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = OAuthRepository(session)
        self._resource = mcp_resource_url(settings)

    # Clients (RFC 7591) -------------------------------------------------------------

    async def register_client(self, info: OAuthClientInformationFull) -> None:
        uris = info.redirect_uris or []
        if len(uris) > MAX_REDIRECT_URIS:
            raise RegistrationError("invalid_redirect_uri", "Too many redirect URIs")
        for uri in uris:
            # https anywhere, or http back to the user's own machine (desktop clients); never
            # userinfo or a fragment, which would let the shown host differ from the real one.
            secure = uri.scheme == "https" or (uri.scheme == "http" and uri.host in LOOPBACK_HOSTS)
            if not secure or uri.username or uri.password or uri.fragment:
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "Redirect URIs must use https (or http on localhost), "
                    "without credentials or a fragment",
                )
        metadata = info.model_dump(mode="json", exclude={"client_secret"})
        if len(json.dumps(metadata)) > MAX_METADATA_BYTES:
            raise RegistrationError("invalid_client_metadata", "Client metadata is too large")
        try:
            box = SecretBox(self._settings) if info.client_secret else None
        except SecretsNotConfiguredError as exc:
            raise RegistrationError(
                "invalid_client_metadata",
                "This server can't store client secrets yet; "
                "register with token_endpoint_auth_method 'none'",
            ) from exc
        self._repo.add(
            OAuthClient(
                id=info.client_id,
                client_name=(info.client_name or "")[:200] or None,
                client_metadata=metadata,
                secret_encrypted=box.encrypt(info.client_secret)
                if box and info.client_secret
                else None,
            )
        )
        await self._session.commit()
        logger.info("oauth_client_registered", client_id=info.client_id)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = await self._repo.client(client_id)
        if row is None:
            return None
        secret = (
            SecretBox(self._settings).decrypt(row.secret_encrypted)
            if row.secret_encrypted
            else None
        )
        return OAuthClientInformationFull.model_validate(
            {**row.client_metadata, "client_secret": secret}
        )

    # Authorization and consent ------------------------------------------------------

    async def start_authorization(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the consent page."""
        if params.resource is not None and not _same_resource(params.resource, self._resource):
            raise AuthorizeError("invalid_target", "Unknown resource")
        if not S256_CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError("invalid_request", "code_challenge must be an S256 challenge")
        scopes = params.scopes or [SCOPE]
        if set(scopes) != {SCOPE}:
            raise AuthorizeError("invalid_scope", f"The only scope is '{SCOPE}'")
        request = OAuthAuthorizationRequest(
            client_id=client.client_id,
            scopes=scopes,
            state=params.state,
            code_challenge=params.code_challenge,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_explicit=params.redirect_uri_provided_explicitly,
            resource=self._resource,
            expires_at=datetime.now(UTC) + REQUEST_TTL,
        )
        self._repo.add(request)
        await self._session.commit()
        return f"{self._settings.frontend_url.rstrip('/')}/oauth/consent?request={request.id}"

    async def describe(self, request_id: uuid.UUID) -> ConsentRequestRead:
        request = await self._repo.request(request_id)
        if request is None or request.expires_at <= datetime.now(UTC):
            raise NotFoundError(EXPIRED_REQUEST)
        client = await self._repo.client(request.client_id)
        return ConsentRequestRead(
            id=request.id,
            client_name=(client.client_name if client else None) or "An unnamed app",
            redirect_host=_host(request.redirect_uri),
            expires_at=request.expires_at,
        )

    async def approve(self, request_id: uuid.UUID, user: User) -> str:
        """Grant the client access as `user`; returns where to send the browser."""
        now = datetime.now(UTC)
        request = await self._repo.take_request(request_id, at=now)
        if request is None:
            raise NotFoundError(EXPIRED_REQUEST)
        # Connecting the same app again replaces the earlier connection.
        await self._repo.revoke_grants(user.id, request.client_id, at=now)
        grant = OAuthGrant(
            id=uuid.uuid4(),
            user_id=user.id,
            client_id=request.client_id,
            scopes=request.scopes,
            resource=request.resource,
            redirect_uri=request.redirect_uri,
        )
        self._repo.add(grant)
        await self._session.flush()
        code = secrets.token_urlsafe(32)
        self._repo.add(
            OAuthAuthorizationCode(
                code_hash=hash_secret(code),
                grant_id=grant.id,
                code_challenge=request.code_challenge,
                redirect_uri=request.redirect_uri,
                redirect_uri_explicit=request.redirect_uri_explicit,
                expires_at=now + CODE_TTL,
            )
        )
        await self._session.commit()
        logger.info("oauth_consent_granted", user_id=str(user.id), client_id=request.client_id)
        return construct_redirect_uri(request.redirect_uri, code=code, state=request.state)

    async def deny(self, request_id: uuid.UUID) -> str:
        request = await self._repo.take_request(request_id, at=datetime.now(UTC))
        if request is None:
            raise NotFoundError(EXPIRED_REQUEST)
        await self._session.commit()
        return construct_redirect_uri(
            request.redirect_uri,
            error="access_denied",
            error_description="The user declined access",
            state=request.state,
        )

    # Tokens -------------------------------------------------------------------------

    async def load_code(
        self, client: OAuthClientInformationFull, raw_code: str
    ) -> AuthorizationCode | None:
        found = await self._repo.code(hash_secret(raw_code))
        if found is None:
            return None
        code, grant = found
        if grant.client_id != client.client_id or grant.revoked_at is not None:
            return None
        if code.used_at is not None:
            # A code came back: someone has a copy, so the tokens it bought can't be trusted.
            await self._revoke(grant.id, reason="code_replayed")
            return None
        return AuthorizationCode(
            code=raw_code,
            scopes=grant.scopes,
            expires_at=code.expires_at.timestamp(),
            client_id=grant.client_id,
            code_challenge=code.code_challenge,
            redirect_uri=AnyUrl(code.redirect_uri),
            redirect_uri_provided_explicitly=code.redirect_uri_explicit,
            resource=grant.resource,
            subject=str(grant.user_id),
        )

    async def exchange_code(self, code: AuthorizationCode) -> TokenResponse:
        now = datetime.now(UTC)
        code_hash = hash_secret(code.code)
        grant_id = await self._repo.use_code(code_hash, at=now)
        if grant_id is None:
            # Another exchange of this code won the race: still a replay.
            found = await self._repo.code(code_hash)
            if found is not None and found[0].used_at is not None:
                await self._revoke(found[1].id, reason="code_replayed")
        grant = await self._repo.grant(grant_id, for_update=True) if grant_id else None
        if grant is None or not await self._usable(grant):
            await self._session.commit()
            raise TokenError("invalid_grant", "The authorization code is no longer valid")
        return await self._issue(grant, now=now)

    async def load_refresh(
        self, client: OAuthClientInformationFull, raw_token: str
    ) -> RefreshToken | None:
        found = await self._repo.token(hash_secret(raw_token), OAuthTokenKind.REFRESH)
        if found is None:
            return None
        token, grant, user = found
        if grant.client_id != client.client_id or grant.revoked_at is not None:
            return None
        if token.revoked_at is not None:
            if datetime.now(UTC) - token.revoked_at > REFRESH_REUSE_GRACE:
                # A rotated refresh token came back: someone holds a copy, so the whole
                # connection ends.
                await self._revoke(grant.id, reason="refresh_token_reused")
            return None
        if not user.is_active:
            return None
        return RefreshToken(
            token=raw_token,
            client_id=grant.client_id,
            scopes=grant.scopes,
            expires_at=_timestamp(token.expires_at),
            resource=grant.resource,
            subject=str(grant.user_id),
        )

    async def exchange_refresh(self, refresh: RefreshToken) -> TokenResponse:
        now = datetime.now(UTC)
        grant_id = await self._repo.claim_refresh(hash_secret(refresh.token), at=now)
        grant = await self._repo.grant(grant_id, for_update=True) if grant_id else None
        if grant is None or not await self._usable(grant):
            await self._session.commit()
            raise TokenError("invalid_grant", "The refresh token is no longer valid")
        return await self._issue(grant, now=now)

    async def verify_access(self, raw_token: str) -> AccessToken | None:
        """Checked on every MCP request, so revoking the grant or deactivating the user
        takes effect immediately."""
        found = await self._repo.token(hash_secret(raw_token), OAuthTokenKind.ACCESS)
        if found is None:
            return None
        token, grant, user = found
        now = datetime.now(UTC)
        if token.expires_at <= now or grant.revoked_at is not None or not user.is_active:
            return None
        await self._repo.touch_grant(grant.id, at=now, older_than=now - TOUCH_EVERY)
        await self._session.commit()
        return AccessToken(
            token=raw_token,
            client_id=grant.client_id,
            scopes=grant.scopes,
            expires_at=_timestamp(token.expires_at),
            resource=grant.resource,
            subject=str(user.id),
        )

    async def revoke_token(self, raw_token: str) -> None:
        """RFC 7009: revoking either token ends the whole connection."""
        token_hash = hash_secret(raw_token)
        for kind in OAuthTokenKind:
            found = await self._repo.token(token_hash, kind)
            if found is not None:
                await self._revoke(found[1].id, reason="client_revoked")
                return

    async def _usable(self, grant: OAuthGrant) -> bool:
        if grant.revoked_at is not None:
            return False
        user = await self._session.get(User, grant.user_id)
        return user is not None and user.is_active

    async def _issue(self, grant: OAuthGrant, *, now: datetime) -> TokenResponse:
        access_ttl = timedelta(minutes=self._settings.mcp_access_ttl_minutes)
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        for raw, kind, expires_at in (
            (access, OAuthTokenKind.ACCESS, now + access_ttl),
            (
                refresh,
                OAuthTokenKind.REFRESH,
                now + timedelta(days=self._settings.mcp_refresh_ttl_days),
            ),
        ):
            self._repo.add(
                OAuthToken(
                    token_hash=hash_secret(raw), grant_id=grant.id, kind=kind, expires_at=expires_at
                )
            )
        grant.last_used_at = now
        await self._session.commit()
        return TokenResponse(
            access_token=access,
            expires_in=int(access_ttl.total_seconds()),
            refresh_token=refresh,
            scope=" ".join(grant.scopes),
        )

    async def _revoke(self, grant_id: uuid.UUID, *, reason: str) -> None:
        await self._repo.revoke_grant(grant_id, at=datetime.now(UTC))
        await self._session.commit()
        logger.warning("oauth_grant_revoked", grant_id=str(grant_id), reason=reason)

    # Connected apps (the user's own view) -------------------------------------------

    async def connected_apps(self, user: User) -> list[ConnectedAppRead]:
        apps = []
        for grant, client in await self._repo.grants_for(user.id):
            apps.append(
                ConnectedAppRead(
                    id=grant.id,
                    client_name=client.client_name or "An unnamed app",
                    redirect_host=_host(grant.redirect_uri),
                    created_at=grant.created_at,
                    last_used_at=grant.last_used_at,
                )
            )
        return apps

    async def disconnect(self, user: User, grant_id: uuid.UUID) -> None:
        grant = await self._repo.grant(grant_id)
        if grant is None or grant.user_id != user.id or grant.revoked_at is not None:
            raise NotFoundError("Connected app not found")
        await self._revoke(grant.id, reason="user_disconnected")

    async def prune(self) -> dict[str, int]:
        now = datetime.now(UTC)
        counts = await self._repo.prune(
            now=now, cutoff=now - PRUNE_MARGIN, unused_client_cutoff=now - UNUSED_CLIENT_TTL
        )
        await self._session.commit()
        return counts
