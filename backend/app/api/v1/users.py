import uuid

from fastapi import APIRouter, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.schemas.user import UserCreate, UserRead, UserSummary, UserUpdate
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_users(_: AdminUser, session: SessionDep) -> list[UserRead]:
    """All users with full details (admin)."""
    users = await UserService(session).list_users()
    return [UserRead.model_validate(u) for u in users]


@router.get("/assignable")
async def list_assignable_users(_: CurrentUser, session: SessionDep) -> list[UserSummary]:
    """Active users a lead can be assigned to. Open to every signed-in user."""
    users = await UserService(session).list_users(active_only=True)
    return [UserSummary.model_validate(u) for u in users]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, admin: AdminUser, session: SessionDep) -> UserRead:
    user = await UserService(session).create_user(body, actor=admin)
    return UserRead.model_validate(user)


@router.get("/{user_id}")
async def get_user(user_id: uuid.UUID, _: AdminUser, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await UserService(session).get_user(user_id))


@router.patch("/{user_id}")
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, admin: AdminUser, session: SessionDep
) -> UserRead:
    user = await UserService(session).update_user(user_id, body, actor=admin)
    return UserRead.model_validate(user)
