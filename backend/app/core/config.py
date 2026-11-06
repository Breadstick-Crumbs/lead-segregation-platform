"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Lead Segregation Platform"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    database_url: PostgresDsn = PostgresDsn("postgresql+asyncpg://leads:leads@localhost:5432/leads")

    jwt_secret: SecretStr = SecretStr("change-me")
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 14
    secrets_encryption_key: SecretStr | None = None  # Fernet key for mailbox credentials

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6-luna"

    whatsapp_graph_version: str = "v26.0"
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None

    sentry_dsn: str | None = None

    @model_validator(mode="after")
    def _reject_insecure_production_defaults(self) -> "Settings":
        if self.environment == "production" and self.jwt_secret.get_secret_value() == "change-me":
            raise ValueError("JWT_SECRET must be set in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
