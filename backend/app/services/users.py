"""User management. Admin-only operations are enforced by the API layer."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import hash_password_async, verify_password_async
from app.models import User
from app.models.enums import Role
from app.repositories.users import RefreshTokenRepository, UserRepository
from app.schemas.user import PasswordChange, UserCreate, UserUpdate

logger = get_logger(__name__)


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._tokens = RefreshTokenRepository(session)

    async def list_users(self, *, active_only: bool = False) -> Sequence[User]:
        return await self._users.list_all(active_only=active_only)

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def create_user(self, data: UserCreate, *, actor: User) -> User:
        if await self._users.get_by_email(data.email) is not None:
            raise ConflictError("A user with this email already exists")
        user = User(
            email=data.email,
            full_name=data.full_name,
            role=data.role,
            password_hash=(
                await hash_password_async(data.password) if data.password is not None else None
            ),
        )
        self._users.add(user)
        try:
            await self._session.commit()
        except IntegrityError as exc:  # lost a race with a concurrent create
            await self._session.rollback()
            raise ConflictError("A user with this email already exists") from exc
        logger.info("user_created", user_id=str(user.id), role=user.role, actor_id=str(actor.id))
        return user

    async def update_user(self, user_id: uuid.UUID, data: UserUpdate, *, actor: User) -> User:
        user = await self.get_user(user_id)
        removes_admin = (
            user.role == Role.ADMIN
            and user.is_active
            and (data.is_active is False or data.role not in (None, Role.ADMIN))
        )
        if removes_admin:
            if user.id == actor.id:
                raise ConflictError("You can't deactivate or demote your own account")
            # Locked so two admins demoting each other at once can't leave zero admins.
            if len(await self._users.lock_active_admin_ids()) <= 1:
                raise ConflictError("At least one active admin is required")

        sign_out = False
        if data.full_name is not None:
            user.full_name = data.full_name
        if data.role is not None:
            user.role = data.role
        if data.is_active is not None:
            sign_out = sign_out or (user.is_active and not data.is_active)
            user.is_active = data.is_active
        if data.password is not None:
            user.password_hash = await hash_password_async(data.password)
            sign_out = True
        if sign_out:
            await self._tokens.revoke_all_for_user(user.id, at=datetime.now(UTC))

        await self._session.commit()
        logger.info("user_updated", user_id=str(user.id), actor_id=str(actor.id))
        return user

    async def change_own_password(
        self, user: User, data: PasswordChange, *, keep_session_family: uuid.UUID | None
    ) -> None:
        """Change the caller's password and sign out every *other* session."""
        if not await verify_password_async(data.current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect")
        user.password_hash = await hash_password_async(data.new_password)
        await self._tokens.revoke_all_for_user(
            user.id, at=datetime.now(UTC), keep_family=keep_session_family
        )
        await self._session.commit()
        logger.info("password_changed", user_id=str(user.id))
