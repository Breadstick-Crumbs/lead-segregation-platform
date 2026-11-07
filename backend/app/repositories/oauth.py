import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthGrant,
    OAuthToken,
    User,
)
from app.models.oauth import OAuthTokenKind


class OAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        row: OAuthClient
        | OAuthAuthorizationRequest
        | OAuthGrant
        | OAuthAuthorizationCode
        | OAuthToken,
    ) -> None:
        self._session.add(row)

    async def client(self, client_id: str) -> OAuthClient | None:
        return await self._session.get(OAuthClient, client_id)

    async def request(self, request_id: uuid.UUID) -> OAuthAuthorizationRequest | None:
        return await self._session.get(OAuthAuthorizationRequest, request_id)

    async def take_request(
        self, request_id: uuid.UUID, *, at: datetime
    ) -> OAuthAuthorizationRequest | None:
        """Delete a live request and return it: a consent can only be decided once."""
        result = await self._session.execute(
            delete(OAuthAuthorizationRequest)
            .where(
                OAuthAuthorizationRequest.id == request_id,
                OAuthAuthorizationRequest.expires_at > at,
            )
            .returning(OAuthAuthorizationRequest)
        )
        return result.scalar_one_or_none()

    async def code(self, code_hash: str) -> tuple[OAuthAuthorizationCode, OAuthGrant] | None:
        row = (
            await self._session.execute(
                select(OAuthAuthorizationCode, OAuthGrant)
                .join(OAuthGrant, OAuthGrant.id == OAuthAuthorizationCode.grant_id)
                .where(OAuthAuthorizationCode.code_hash == code_hash)
            )
        ).one_or_none()
        return (row[0], row[1]) if row else None

    async def use_code(self, code_hash: str, *, at: datetime) -> uuid.UUID | None:
        """Mark a live code used and return its grant. One conditional UPDATE, so two
        concurrent exchanges of the same code can't both succeed."""
        result = await self._session.execute(
            update(OAuthAuthorizationCode)
            .where(
                OAuthAuthorizationCode.code_hash == code_hash,
                OAuthAuthorizationCode.used_at.is_(None),
                OAuthAuthorizationCode.expires_at > at,
            )
            .values(used_at=at)
            .returning(OAuthAuthorizationCode.grant_id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none()

    async def token(
        self, token_hash: str, kind: OAuthTokenKind
    ) -> tuple[OAuthToken, OAuthGrant, User] | None:
        row = (
            await self._session.execute(
                select(OAuthToken, OAuthGrant, User)
                .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
                .join(User, User.id == OAuthGrant.user_id)
                .where(OAuthToken.token_hash == token_hash, OAuthToken.kind == kind)
            )
        ).one_or_none()
        return (row[0], row[1], row[2]) if row else None

    async def claim_refresh(self, token_hash: str, *, at: datetime) -> uuid.UUID | None:
        """Atomically revoke a live refresh token and return its grant (rotation)."""
        result = await self._session.execute(
            update(OAuthToken)
            .where(
                OAuthToken.token_hash == token_hash,
                OAuthToken.kind == OAuthTokenKind.REFRESH,
                OAuthToken.revoked_at.is_(None),
                OAuthToken.expires_at > at,
            )
            .values(revoked_at=at)
            .returning(OAuthToken.grant_id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none()

    async def grant(self, grant_id: uuid.UUID, *, for_update: bool = False) -> OAuthGrant | None:
        return await self._session.get(OAuthGrant, grant_id, with_for_update=for_update)

    async def grants_for(self, user_id: uuid.UUID) -> Sequence[tuple[OAuthGrant, OAuthClient]]:
        rows = await self._session.execute(
            select(OAuthGrant, OAuthClient)
            .join(OAuthClient, OAuthClient.id == OAuthGrant.client_id)
            .where(OAuthGrant.user_id == user_id, OAuthGrant.revoked_at.is_(None))
            .order_by(OAuthGrant.created_at.desc())
        )
        return [(grant, client) for grant, client in rows]

    async def revoke_grant(self, grant_id: uuid.UUID, *, at: datetime) -> None:
        await self._session.execute(
            update(OAuthGrant)
            .where(OAuthGrant.id == grant_id, OAuthGrant.revoked_at.is_(None))
            .values(revoked_at=at)
            .execution_options(synchronize_session=False)
        )

    async def revoke_grants(self, user_id: uuid.UUID, client_id: str, *, at: datetime) -> None:
        await self._session.execute(
            update(OAuthGrant)
            .where(
                OAuthGrant.user_id == user_id,
                OAuthGrant.client_id == client_id,
                OAuthGrant.revoked_at.is_(None),
            )
            .values(revoked_at=at)
            .execution_options(synchronize_session=False)
        )

    async def touch_grant(self, grant_id: uuid.UUID, *, at: datetime, older_than: datetime) -> None:
        await self._session.execute(
            update(OAuthGrant)
            .where(
                OAuthGrant.id == grant_id,
                (OAuthGrant.last_used_at.is_(None)) | (OAuthGrant.last_used_at < older_than),
            )
            .values(last_used_at=at)
            .execution_options(synchronize_session=False)
        )

    async def prune(
        self, *, now: datetime, cutoff: datetime, unused_client_cutoff: datetime
    ) -> dict[str, int]:
        """Delete expired requests, codes and tokens, and registered clients nobody ever
        approved. `cutoff` keeps a margin so a just-expired row is still there to explain an
        error. Grants stay (they're small), so an approved client is never pruned."""
        counts: dict[str, int] = {}
        statements = {
            "requests": delete(OAuthAuthorizationRequest).where(
                OAuthAuthorizationRequest.expires_at < now
            ),
            "codes": delete(OAuthAuthorizationCode).where(
                OAuthAuthorizationCode.expires_at < cutoff
            ),
            "tokens": delete(OAuthToken).where(OAuthToken.expires_at < cutoff),
            "clients": delete(OAuthClient).where(
                OAuthClient.created_at < unused_client_cutoff,
                ~select(OAuthGrant.id).where(OAuthGrant.client_id == OAuthClient.id).exists(),
                ~select(OAuthAuthorizationRequest.id)
                .where(OAuthAuthorizationRequest.client_id == OAuthClient.id)
                .exists(),
            ),
        }
        for name, statement in statements.items():
            result = await self._session.execute(
                statement.execution_options(synchronize_session=False)
            )
            counts[name] = result.rowcount  # type: ignore[attr-defined]
        return counts
