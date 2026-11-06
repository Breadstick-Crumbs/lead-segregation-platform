import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(environment="production")


def test_production_accepts_explicit_jwt_secret() -> None:
    settings = Settings(environment="production", jwt_secret=SecretStr("a-real-secret"))

    assert settings.is_production
