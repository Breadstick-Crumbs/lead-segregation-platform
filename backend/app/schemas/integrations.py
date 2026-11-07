"""Integration settings as the admin screen reads and writes them. Secrets go in, never out."""

import re
import uuid
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, Field, SecretStr

PLAIN_FIELDS = ("whatsapp_phone_number_id", "entra_tenant_id", "entra_client_id")
SECRET_FIELDS = (
    "whatsapp_access_token",
    "whatsapp_app_secret",
    "whatsapp_verify_token",
    "slack_webhook_url",
    "teams_webhook_url",
    "entra_client_secret",
)


class FieldState(BaseModel):
    value: str | None = Field(description="The value in use; always null for secrets")
    source: Literal["dashboard", "environment"] | None = Field(
        description="Where the value in use comes from; null when it isn't set"
    )


class IntegrationConfig(BaseModel):
    fields: dict[str, FieldState]
    whatsapp_webhook_url: str = Field(description="The callback URL to give Meta")
    microsoft_redirect_uri: str = Field(description="The redirect URI to register in Entra")
    secrets_key: bool = Field(description="Whether secrets can be saved (SECRETS_ENCRYPTION_KEY)")


def _digits(value: str) -> str:
    if not value.isdigit():
        raise ValueError("Phone number ID is the numeric ID from Meta, not the phone number")
    return value


def _guid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        # The ID token's issuer always uses the tenant GUID, so a domain wouldn't verify.
        raise ValueError("Use the Directory (tenant) ID GUID from Entra, not a domain") from None


def _https_url(prefixes: tuple[str, ...]) -> AfterValidator:
    def check(value: SecretStr) -> SecretStr:
        url = value.get_secret_value()
        if not url.startswith(prefixes):
            raise ValueError(f"Expected a URL starting with {' or '.join(prefixes)}")
        return value

    return AfterValidator(check)


# Where Teams "Workflows" (Power Automate) and legacy incoming webhooks live. The URL is
# posted to from inside the server, so an arbitrary host would let an admin session probe
# internal services (SSRF).
TEAMS_WEBHOOK_HOSTS = (".logic.azure.com", ".powerplatform.com", ".webhook.office.com")


def _teams_url(value: SecretStr) -> SecretStr:
    parts = urlsplit(value.get_secret_value())
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.port not in (None, 443)
        or not host.endswith(TEAMS_WEBHOOK_HOSTS)
    ):
        raise ValueError(
            "Expected a Teams Workflows webhook URL (https on "
            + ", ".join(f"*{h}" for h in TEAMS_WEBHOOK_HOSTS)
            + ")"
        )
    return value


def _token(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not raw or re.search(r"\s", raw):
        raise ValueError("Must not be empty or contain spaces")
    return value


Token = Annotated[SecretStr, Field(max_length=1000), AfterValidator(_token)]


class IntegrationUpdate(BaseModel):
    """Send only the fields to change. A value sets it; `null` removes the dashboard's value,
    so the server's environment setting (if any) applies again."""

    whatsapp_phone_number_id: (
        Annotated[str, Field(max_length=40), AfterValidator(_digits)] | None
    ) = None
    whatsapp_access_token: Token | None = None
    whatsapp_app_secret: Token | None = None
    whatsapp_verify_token: Token | None = None
    slack_webhook_url: (
        Annotated[SecretStr, Field(max_length=500), _https_url(("https://hooks.slack.com/",))]
        | None
    ) = None
    teams_webhook_url: (
        Annotated[SecretStr, Field(max_length=1000), AfterValidator(_teams_url)] | None
    ) = None
    entra_tenant_id: Annotated[str, AfterValidator(_guid)] | None = None
    entra_client_id: Annotated[str, AfterValidator(_guid)] | None = None
    entra_client_secret: Token | None = None


class ConnectionCheck(BaseModel):
    ok: bool
    detail: str
