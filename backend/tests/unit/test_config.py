import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _no_ambient_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)


class _Settings(Settings):
    # Ignore backend/.env so the result depends only on what the test passes.
    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET must be set"):
        _Settings(environment="production")


def test_production_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        _Settings(environment="production", jwt_secret=SecretStr("too-short"))


def test_production_accepts_a_strong_jwt_secret() -> None:
    settings = _Settings(environment="production", jwt_secret=SecretStr("x" * 32))

    assert settings.is_production


def test_development_allows_the_placeholder_secret() -> None:
    assert not _Settings().is_production
