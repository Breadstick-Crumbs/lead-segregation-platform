"""OAuth 2.1 authorization server state for MCP clients (Claude, ChatGPT and others).

A client registers itself (dynamic client registration), a signed-in user approves it on
the consent page, and that approval is a grant. Codes and tokens hang off the grant, so
revoking the grant cuts the client off at once. Codes and tokens are stored as SHA-256
hashes, never in the clear."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, str_enum, tz_datetime


class OAuthTokenKind(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class OAuthClient(CreatedAtMixin, Base):
    __tablename__ = "oauth_clients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # the client_id
    client_name: Mapped[str | None] = mapped_column(String(200))
    # Registered metadata (RFC 7591), without the secret.
    client_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    # Fernet-encrypted: the token endpoint compares the secret, so it can't be a hash.
    secret_encrypted: Mapped[str | None] = mapped_column(Text)


class OAuthAuthorizationRequest(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """An /authorize request waiting for the user's decision on the consent page."""

    __tablename__ = "oauth_authorization_requests"

    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.id", ondelete="CASCADE"), index=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    state: Mapped[str | None] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(Text)
    redirect_uri_explicit: Mapped[bool]
    resource: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(tz_datetime())


class OAuthGrant(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One user's approval of one client: a "connected app". Kept after revocation, so a
    client that was ever approved survives pruning and can reconnect."""

    __tablename__ = "oauth_grants"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.id", ondelete="CASCADE"), index=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    resource: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text)  # the one the user approved
    last_used_at: Mapped[datetime | None] = mapped_column(tz_datetime())
    revoked_at: Mapped[datetime | None] = mapped_column(tz_datetime())


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_authorization_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oauth_grants.id", ondelete="CASCADE"), index=True
    )
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(Text)
    redirect_uri_explicit: Mapped[bool]
    expires_at: Mapped[datetime] = mapped_column(tz_datetime())
    # Set on exchange. A second exchange of the same code revokes the grant (RFC 6749 §4.1.2).
    used_at: Mapped[datetime | None] = mapped_column(tz_datetime())


class OAuthToken(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "oauth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oauth_grants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[OAuthTokenKind] = mapped_column(str_enum(OAuthTokenKind, "oauth_token_kind"))
    expires_at: Mapped[datetime] = mapped_column(tz_datetime())
    # Refresh tokens are revoked on rotation; one that comes back revokes the grant.
    revoked_at: Mapped[datetime | None] = mapped_column(tz_datetime())
