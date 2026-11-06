import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.enums import Role
from app.schemas.user import UserUpdate
from app.services.users import UserService
from tests.integration.conftest import CreateUser, bearer, login

pytestmark = pytest.mark.integration

NEW_REP = {
    "email": "Omar.Khalid@Example.com",
    "full_name": "  Omar Khalid ",
    "role": "sales_rep",
    "password": "long-enough-password",
}


async def _admin_token(api: AsyncClient, create_user: CreateUser) -> str:
    await create_user("admin@tawasol.test", role=Role.ADMIN, full_name="Admin")
    return await login(api, "admin@tawasol.test")


async def test_admin_creates_a_user(api: AsyncClient, create_user: CreateUser) -> None:
    token = await _admin_token(api, create_user)

    response = await api.post("/api/v1/users", headers=bearer(token), json=NEW_REP)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "omar.khalid@example.com"
    assert body["full_name"] == "Omar Khalid"
    assert body["role"] == "sales_rep"
    await login(api, "omar.khalid@example.com", "long-enough-password")


async def test_duplicate_email_is_a_conflict(api: AsyncClient, create_user: CreateUser) -> None:
    token = await _admin_token(api, create_user)
    await api.post("/api/v1/users", headers=bearer(token), json=NEW_REP)

    response = await api.post("/api/v1/users", headers=bearer(token), json=NEW_REP)

    assert response.status_code == 409


@pytest.mark.parametrize(
    "overrides",
    [{"password": "short"}, {"email": "not-an-email"}, {"role": "superuser"}, {"full_name": " "}],
)
async def test_invalid_user_input_is_rejected(
    api: AsyncClient, create_user: CreateUser, overrides: dict[str, str]
) -> None:
    token = await _admin_token(api, create_user)

    response = await api.post("/api/v1/users", headers=bearer(token), json={**NEW_REP, **overrides})

    assert response.status_code == 422


async def test_sales_rep_cannot_manage_users(api: AsyncClient, create_user: CreateUser) -> None:
    other = await create_user("other@tawasol.test")
    await create_user("rep@tawasol.test")
    token = await login(api, "rep@tawasol.test")

    assert (await api.post("/api/v1/users", headers=bearer(token), json=NEW_REP)).status_code == 403
    patch = await api.patch(
        f"/api/v1/users/{other.id}", headers=bearer(token), json={"is_active": False}
    )
    assert patch.status_code == 403
    assert (await api.get("/api/v1/users", headers=bearer(token))).status_code == 403


async def test_assignable_users_are_active_and_minimal(
    api: AsyncClient, create_user: CreateUser
) -> None:
    await create_user("rep@tawasol.test", full_name="Layla Hassan")
    await create_user("gone@tawasol.test", full_name="Former Rep", is_active=False)
    token = await login(api, "rep@tawasol.test")

    response = await api.get("/api/v1/users/assignable", headers=bearer(token))

    assert response.status_code == 200
    assert [set(u) for u in response.json()] == [{"id", "full_name", "role"}]
    assert response.json()[0]["full_name"] == "Layla Hassan"


async def test_admin_deactivates_a_user_and_signs_them_out(
    api: AsyncClient, create_user: CreateUser
) -> None:
    rep = await create_user("rep@tawasol.test")
    rep_token = await login(api, "rep@tawasol.test")
    api.cookies.clear()
    admin_token = await _admin_token(api, create_user)

    response = await api.patch(
        f"/api/v1/users/{rep.id}", headers=bearer(admin_token), json={"is_active": False}
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert (await api.get("/api/v1/auth/me", headers=bearer(rep_token))).status_code == 401


async def test_admin_cannot_lock_themselves_out(api: AsyncClient, create_user: CreateUser) -> None:
    token = await _admin_token(api, create_user)
    me = (await api.get("/api/v1/auth/me", headers=bearer(token))).json()

    for change in ({"is_active": False}, {"role": "sales_rep"}):
        response = await api.patch(f"/api/v1/users/{me['id']}", headers=bearer(token), json=change)
        assert response.status_code == 409


async def test_the_last_active_admin_cannot_be_removed(
    create_user: CreateUser, db_session: AsyncSession
) -> None:
    only_admin = await create_user("admin@tawasol.test", role=Role.ADMIN)
    actor = await create_user("ops@tawasol.test", role=Role.ADMIN, is_active=False)
    service = UserService(db_session)

    for change in (UserUpdate(is_active=False), UserUpdate(role=Role.SALES_REP)):
        with pytest.raises(ConflictError, match="At least one active admin"):
            await service.update_user(only_admin.id, change, actor=actor)


async def test_an_admin_can_demote_another_when_one_remains(
    api: AsyncClient, create_user: CreateUser
) -> None:
    other = await create_user("second@tawasol.test", role=Role.ADMIN)
    token = await _admin_token(api, create_user)

    response = await api.patch(
        f"/api/v1/users/{other.id}", headers=bearer(token), json={"role": "sales_rep"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "sales_rep"


async def test_unknown_user_is_404(api: AsyncClient, create_user: CreateUser) -> None:
    token = await _admin_token(api, create_user)

    response = await api.patch(
        "/api/v1/users/00000000-0000-0000-0000-000000000000",
        headers=bearer(token),
        json={"full_name": "X"},
    )

    assert response.status_code == 404
