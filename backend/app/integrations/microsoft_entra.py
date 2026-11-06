"""Microsoft Entra ID (OpenID Connect) client: authorization URL, code exchange and ID token
verification for a single-tenant web app.

Uses the v2.0 endpoints directly with httpx and PyJWT; no MSAL, which is synchronous.
"""

import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import anyio
import httpx
import jwt

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

LOGIN_HOST = "https://login.microsoftonline.com"
SCOPES = "openid profile email"
JWKS_TTL_SECONDS = 3600
HTTP_TIMEOUT_SECONDS = 10.0


class EntraError(Exception):
    """Sign-in with Microsoft failed; the message is for logs, not for the user."""


@dataclass(frozen=True)
class EntraIdentity:
    object_id: uuid.UUID
    tenant_id: uuid.UUID
    username: str  # preferred_username: the UPN, administered by the tenant
    name: str | None


@dataclass(frozen=True)
class PkcePair:
    verifier: str
    challenge: str

    @classmethod
    def generate(cls) -> "PkcePair":
        verifier = secrets.token_urlsafe(64)  # 86 chars, within RFC 7636's 43-128
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return cls(verifier=verifier, challenge=challenge)


class EntraClient:
    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # The tenant GUID (not a domain): the ID token issuer always uses the GUID.
        self._tenant_id = tenant_id.lower()
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._transport = transport
        self._jwks: jwt.PyJWKSet | None = None
        self._jwks_fetched_at = 0.0
        self._jwks_lock = anyio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "EntraClient":
        tenant, client, secret = (
            settings.entra_tenant_id,
            settings.entra_client_id,
            settings.entra_client_secret,
        )
        if not (tenant and client and secret):
            raise EntraError("Sign in with Microsoft is not configured")
        return cls(
            tenant_id=tenant,
            client_id=client,
            client_secret=secret.get_secret_value(),
            redirect_uri=settings.entra_callback_url,
        )

    @property
    def _base(self) -> str:
        return f"{LOGIN_HOST}/{self._tenant_id}"

    @property
    def issuer(self) -> str:
        return f"{self._base}/v2.0"

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "response_type": "code",
                "redirect_uri": self._redirect_uri,
                "response_mode": "query",
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{self._base}/oauth2/v2.0/authorize?{query}"

    async def exchange_code(self, code: str, *, code_verifier: str) -> str:
        """Redeem the authorization code; returns the raw ID token."""
        async with self._http() as http:
            response = await http.post(
                f"{self._base}/oauth2/v2.0/token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                    "code_verifier": code_verifier,
                    "scope": SCOPES,
                },
            )
        if response.status_code != httpx.codes.OK:
            # Entra returns {"error": ..., "error_description": ...}; never log the code.
            error = response.json().get("error", "unknown") if response.content else "unknown"
            raise EntraError(f"Token exchange failed: {response.status_code} {error}")
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            raise EntraError("Token response had no id_token")
        return id_token

    async def verify_id_token(self, id_token: str, *, nonce: str) -> EntraIdentity:
        try:
            key = await self._signing_key(jwt.get_unverified_header(id_token).get("kid"))
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss", "oid", "tid", "nonce"]},
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise EntraError(f"Invalid ID token: {exc}") from exc

        if not secrets.compare_digest(str(claims["nonce"]), nonce):
            raise EntraError("ID token nonce mismatch")
        if str(claims["tid"]).lower() != self._tenant_id:
            raise EntraError("ID token is from another tenant")
        username = claims.get("preferred_username")
        if not isinstance(username, str) or "@" not in username:
            raise EntraError("ID token has no usable preferred_username")
        try:
            return EntraIdentity(
                object_id=uuid.UUID(claims["oid"]),
                tenant_id=uuid.UUID(claims["tid"]),
                username=username.lower(),
                name=claims.get("name"),
            )
        except ValueError as exc:
            raise EntraError("ID token has malformed oid/tid") from exc

    async def _signing_key(self, kid: object) -> jwt.PyJWK:
        if not isinstance(kid, str):
            raise EntraError("ID token has no key id")
        async with self._jwks_lock:
            stale = time.monotonic() - self._jwks_fetched_at > JWKS_TTL_SECONDS
            if self._jwks is None or stale or not _has_kid(self._jwks, kid):
                # Entra rotates keys; an unknown kid triggers one refetch.
                self._jwks = await self._fetch_jwks()
                self._jwks_fetched_at = time.monotonic()
        try:
            return self._jwks[kid]
        except KeyError as exc:
            raise EntraError("ID token signed with an unknown key") from exc

    async def _fetch_jwks(self) -> jwt.PyJWKSet:
        async with self._http() as http:
            response = await http.get(f"{self._base}/discovery/v2.0/keys")
        if response.status_code != httpx.codes.OK:
            raise EntraError(f"JWKS fetch failed: {response.status_code}")
        return jwt.PyJWKSet.from_dict(response.json())

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=HTTP_TIMEOUT_SECONDS)


def _has_kid(jwks: jwt.PyJWKSet, kid: str) -> bool:
    return any(key.key_id == kid for key in jwks.keys)
