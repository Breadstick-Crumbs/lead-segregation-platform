"""The MCP endpoint (`/mcp`, Streamable HTTP) and its OAuth authorization server, as one
ASGI app mounted into FastAPI. Both are served from the dashboard's own origin
(`FRONTEND_URL`), which proxies these paths to the API, so the issuer, the resource and
the consent page share one public URL."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.applications import Starlette

from app.core.config import Settings
from app.mcp_server.tools import INSTRUCTIONS, LeadTools, SessionScope
from app.services.oauth import SCOPE, OAuthService, mcp_resource_url

# Paths the reverse proxy must send to the API (see frontend/nginx.conf and vite.config.ts).
T = TypeVar("T")

PUBLIC_PATHS = ("/mcp", "/.well-known/oauth-", "/authorize", "/token", "/register", "/revoke")


class LeadsOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Adapts the SDK's provider interface to `OAuthService`, one session per call."""

    def __init__(self, session_scope: SessionScope, settings: Callable[[], Settings]) -> None:
        self._session_scope = session_scope
        self._settings = settings

    async def _call(self, work: Callable[[OAuthService], Awaitable[T]]) -> T:
        # The SDK's error types are frozen dataclasses, and contextlib can't set the
        # traceback of one raised through a context manager, so they're carried out of the
        # session block and raised after it.
        error: RegistrationError | AuthorizeError | TokenError
        async with self._session_scope() as session:
            try:
                return await work(OAuthService(session, self._settings()))
            except (RegistrationError, AuthorizeError, TokenError) as exc:
                error = exc
        raise error

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self._call(lambda oauth: oauth.get_client(client_id))

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._call(lambda oauth: oauth.register_client(client_info))

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        return await self._call(lambda oauth: oauth.start_authorization(client, params))

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return await self._call(lambda oauth: oauth.load_code(client, authorization_code))

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        return await self._call(lambda oauth: oauth.exchange_code(authorization_code))

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return await self._call(lambda oauth: oauth.load_refresh(client, refresh_token))

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # There is one scope, so a narrower request can only ask for what the grant has.
        return await self._call(lambda oauth: oauth.exchange_refresh(refresh_token))

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await self._call(lambda oauth: oauth.verify_access(token))

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._call(lambda oauth: oauth.revoke_token(token.token))


def build_mcp_app(
    settings: Settings, session_scope: SessionScope, current_settings: Callable[[], Settings]
) -> tuple[Starlette, StreamableHTTPSessionManager]:
    """The ASGI app and the session manager whose `run()` must wrap the app's lifespan."""
    issuer = settings.frontend_url.rstrip("/")
    server: MCPServer[object] = MCPServer(
        "tawasol-leads",
        title="Tawasol leads",
        instructions=INSTRUCTIONS,
        version="0.1.0",
        auth_server_provider=LeadsOAuthProvider(session_scope, current_settings),
        auth=AuthSettings(
            issuer_url=issuer,
            resource_server_url=mcp_resource_url(settings),
            # Tokens are issued for this resource only, so the check always holds; it guards
            # against a future second resource sharing the issuer.
            validate_token_resource=True,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )
    LeadTools(session_scope, current_settings).register(server)
    app = server.streamable_http_app(
        # Each request stands alone: no session state to lose on a restart, and any API
        # replica can serve any call. The tools don't stream or call back to the client.
        stateless_http=True,
        json_response=True,
        # DNS-rebinding protection is for unauthenticated servers on localhost. Every call
        # here carries a bearer token, which a rebinding page can't have, and browser-based
        # clients (the MCP Inspector) send their own Origin.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return app, server.session_manager
