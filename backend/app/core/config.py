"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PRODUCTION_JWT_SECRET_LENGTH = 32
# Dev-only placeholder (32+ chars so HS256 is not weakened locally); rejected in production.
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Lead Segregation Platform"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # Where the dashboard is served; the Microsoft sign-in callback redirects back here.
    frontend_url: str = "http://localhost:5173"

    database_url: PostgresDsn = PostgresDsn("postgresql+asyncpg://leads:leads@localhost:5442/leads")

    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 14
    secrets_encryption_key: SecretStr | None = None  # Fernet key for mailbox credentials
    # Secure flag on the refresh cookie; defaults to on in production. Set it explicitly for
    # an HTTPS deployment that isn't ENVIRONMENT=production.
    cookie_secure: bool | None = None

    # Sign in with Microsoft (Entra ID, single tenant). Enabled when all three are set.
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None
    # Defaults to <frontend_url>/api/v1/auth/microsoft/callback, so cookies land on the
    # dashboard's origin (the dev server and Caddy both proxy /api to the backend).
    entra_redirect_uri: str | None = None

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6-luna"

    whatsapp_graph_version: str = "v26.0"
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None

    sentry_dsn: str | None = None

    # Used only by `python -m app.scripts.seed`.
    seed_admin_email: str = "admin@tawasol.test"
    seed_admin_password: SecretStr | None = None
    seed_demo_password: SecretStr | None = None  # shared by the demo sales reps

    @model_validator(mode="after")
    def _reject_insecure_production_defaults(self) -> "Settings":
        if self.environment == "production":
            secret = self.jwt_secret.get_secret_value()
            if secret == DEV_JWT_SECRET:
                raise ValueError("JWT_SECRET must be set in production")
            if len(secret) < MIN_PRODUCTION_JWT_SECRET_LENGTH:
                raise ValueError(
                    f"JWT_SECRET must be at least {MIN_PRODUCTION_JWT_SECRET_LENGTH} characters"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def entra_enabled(self) -> bool:
        return bool(self.entra_tenant_id and self.entra_client_id and self.entra_client_secret)

    @property
    def entra_callback_url(self) -> str:
        return self.entra_redirect_uri or (
            f"{self.frontend_url.rstrip('/')}/api/v1/auth/microsoft/callback"
        )

    @property
    def refresh_cookie_secure(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure


@lru_cache
def get_settings() -> Settings:
    return Settings()
