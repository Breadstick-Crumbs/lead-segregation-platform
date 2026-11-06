import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User
from app.models.enums import Role


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_by_entra_oid(self, object_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.entra_oid == object_id))
        return result.scalar_one_or_none()

    async def list_all(self, *, active_only: bool = False) -> Sequence[User]:
        statement = select(User).order_by(User.full_name)
        if active_only:
            statement = statement.where(User.is_active)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def lock_active_admin_ids(self) -> Sequence[uuid.UUID]:
        """Row-lock every active admin, serialising changes that could remove the last one."""
        result = await self._session.execute(
            select(User.id).where(User.role == Role.ADMIN, User.is_active).with_for_update()
        )
        return result.scalars().all()

    async def next_round_robin_rep(self) -> User | None:
        """The active sales rep assigned least recently (never-assigned reps first).

        Serialised with a transaction-level advisory lock: two leads assigned at once can't
        both pick the same rep."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('lead_assignment', 0))")
        )
        rep: User | None = await self._session.scalar(
            select(User)
            .where(User.role == Role.SALES_REP, User.is_active)
            .order_by(User.last_assigned_at.asc().nulls_first(), User.created_at, User.id)
            .limit(1)
        )
        return rep

    def add(self, user: User) -> None:
        self._session.add(user)


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    def add(self, token: RefreshToken) -> None:
        self._session.add(token)

    async def claim(self, token_hash: str, *, at: datetime) -> tuple[uuid.UUID, uuid.UUID] | None:
        """Atomically revoke a live token and return its (user_id, family_id).

        A single conditional UPDATE means two concurrent refreshes with the same token can't
        both succeed: the second waits on the row lock, re-checks `revoked_at IS NULL`, and
        matches nothing.
        """
        result = await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > at,
            )
            .values(revoked_at=at)
            .returning(RefreshToken.user_id, RefreshToken.family_id)
            .execution_options(synchronize_session=False)
        )
        row = result.one_or_none()
        return (row.user_id, row.family_id) if row else None

    async def revoke_family(self, family_id: uuid.UUID, *, at: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
            .execution_options(synchronize_session=False)
        )

    async def revoke_all_for_user(
        self, user_id: uuid.UUID, *, at: datetime, keep_family: uuid.UUID | None = None
    ) -> None:
        statement = update(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
        if keep_family is not None:
            statement = statement.where(RefreshToken.family_id != keep_family)
        await self._session.execute(
            statement.values(revoked_at=at).execution_options(synchronize_session=False)
        )
