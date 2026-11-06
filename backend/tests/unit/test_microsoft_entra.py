import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.integrations.microsoft_entra import EntraError, PkcePair
from tests.fakes.entra import CLIENT_ID, TENANT_ID, FakeEntra, _new_key

NONCE = "the-nonce"


def test_authorization_url_requests_code_with_pkce() -> None:
    url = urlparse(FakeEntra().client().authorization_url(state="s", nonce="n", code_challenge="c"))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}

    assert url.netloc == "login.microsoftonline.com"
    assert url.path == f"/{TENANT_ID}/oauth2/v2.0/authorize"
    assert query["response_type"] == "code"
    assert query["code_challenge_method"] == "S256"
    assert query["client_id"] == CLIENT_ID
    assert {query["state"], query["nonce"], query["code_challenge"]} == {"s", "n", "c"}
    assert set(query["scope"].split()) == {"openid", "profile", "email"}


def test_pkce_challenge_is_the_s256_of_the_verifier() -> None:
    pair = PkcePair.generate()

    assert 43 <= len(pair.verifier) <= 128
    assert pair.challenge != pair.verifier
    assert "=" not in pair.challenge


async def test_valid_id_token_yields_the_identity() -> None:
    fake = FakeEntra(username="Layla.Hassan@Tawasol.test")

    identity = await fake.client().verify_id_token(fake.id_token(nonce=NONCE), nonce=NONCE)

    assert identity.object_id == fake.object_id
    assert str(identity.tenant_id) == TENANT_ID
    assert identity.username == "layla.hassan@tawasol.test"


@pytest.mark.parametrize(
    ("tweak", "message"),
    [
        ({"audience": "someone-elses-app"}, "Invalid ID token"),
        ({"tenant_id": "99999999-2222-3333-4444-555555555555"}, "Invalid ID token"),
        ({"expires_in": timedelta(minutes=-10)}, "Invalid ID token"),
        ({"username": "no-at-sign"}, "preferred_username"),
    ],
)
async def test_untrustworthy_id_tokens_are_rejected(tweak: dict[str, object], message: str) -> None:
    fake = FakeEntra()
    client = fake.client()
    for attr, value in tweak.items():
        setattr(fake, attr, value)

    with pytest.raises(EntraError, match=message):
        await client.verify_id_token(fake.id_token(nonce=NONCE), nonce=NONCE)


async def test_nonce_must_match() -> None:
    fake = FakeEntra()

    with pytest.raises(EntraError, match="nonce"):
        await fake.client().verify_id_token(fake.id_token(nonce="replayed"), nonce=NONCE)


async def test_token_signed_by_another_key_is_rejected() -> None:
    fake = FakeEntra()

    with pytest.raises(EntraError, match="Invalid ID token"):
        await fake.client().verify_id_token(
            fake.id_token(nonce=NONCE, signing_key=_new_key()), nonce=NONCE
        )


async def test_jwks_is_cached_and_refetched_on_key_rotation() -> None:
    fake = FakeEntra()
    client = fake.client()

    await client.verify_id_token(fake.id_token(nonce=NONCE), nonce=NONCE)
    await client.verify_id_token(fake.id_token(nonce=NONCE), nonce=NONCE)
    assert fake.jwks_requests == 1

    fake.key, fake.kid, fake.object_id = _new_key(), "key-2", uuid.uuid4()
    identity = await client.verify_id_token(fake.id_token(nonce=NONCE), nonce=NONCE)

    assert fake.jwks_requests == 2
    assert identity.object_id == fake.object_id


async def test_failed_code_exchange_raises() -> None:
    with pytest.raises(EntraError, match="Token exchange failed"):
        await FakeEntra().client().exchange_code("unknown-code", code_verifier="v")
