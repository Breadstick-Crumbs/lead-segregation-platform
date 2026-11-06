"""A fake Microsoft Entra ID: real RS256 keys, a JWKS endpoint and a token endpoint that
checks PKCE, served through httpx.MockTransport so no request leaves the process."""

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from app.integrations.microsoft_entra import LOGIN_HOST, EntraClient

TENANT_ID = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT_SECRET = "fake-client-secret"
REDIRECT_URI = "http://testserver/api/v1/auth/microsoft/callback"


def _new_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class FakeEntra:
    key: RSAPrivateKey = field(default_factory=_new_key)
    kid: str = "key-1"
    # What the next ID token will say; tests tweak these.
    object_id: uuid.UUID = field(default_factory=uuid.uuid4)
    username: str = "layla.hassan@tawasol.test"
    tenant_id: str = TENANT_ID
    audience: str = CLIENT_ID
    nonce: str | None = None  # None: echo the nonce the token request implies (set by tests)
    expires_in: timedelta = timedelta(hours=1)
    # PKCE challenges by authorization code, as Entra would remember them.
    challenges: dict[str, str] = field(default_factory=dict)
    jwks_requests: int = 0

    def client(self) -> EntraClient:
        return EntraClient(
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            transport=httpx.MockTransport(self._handle),
        )

    def id_token(self, *, nonce: str, signing_key: RSAPrivateKey | None = None) -> str:
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "iss": f"{LOGIN_HOST}/{self.tenant_id}/v2.0",
            "aud": self.audience,
            "iat": now,
            "nbf": now,
            "exp": now + self.expires_in,
            "oid": str(self.object_id),
            "tid": self.tenant_id,
            "preferred_username": self.username,
            "name": "Layla Hassan",
            "nonce": nonce,
        }
        return jwt.encode(
            claims, signing_key or self.key, algorithm="RS256", headers={"kid": self.kid}
        )

    def jwks(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        base = f"/{TENANT_ID}"
        if request.url.path == f"{base}/discovery/v2.0/keys":
            self.jwks_requests += 1
            return httpx.Response(200, json=self.jwks())
        if request.url.path == f"{base}/oauth2/v2.0/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            expected = self.challenges.get(form.get("code", ""))
            verifier = form.get("code_verifier", "")
            actual = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if expected is None or expected != actual or form.get("client_secret") != CLIENT_SECRET:
                return httpx.Response(400, json={"error": "invalid_grant"})
            assert self.nonce is not None, "set FakeEntra.nonce before the callback"
            return httpx.Response(200, json={"id_token": self.id_token(nonce=self.nonce)})
        return httpx.Response(404)
