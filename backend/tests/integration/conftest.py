"""Integration fixtures: a fresh `<db>_test` database built by running the real migrations,
and a per-test session inside a transaction that is rolled back afterwards."""

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import get_session
from app.main import create_app
from app.models import User
from app.models.enums import Role

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_PASSWORD = "correct-horse-battery"

CreateUser = Callable[..., Awaitable[User]]


def _test_db_url() -> URL:
    url = make_url(str(get_settings().database_url))
    return url.set(database=f"{url.database}_test")


async def _recreate_database(url: URL) -> None:
    admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    # CREATE/DROP DATABASE can't run inside a transaction, hence autocommit.
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
        await conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{url.database}"')


def _run_migrations(url: URL) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    url = _test_db_url()
    await _recreate_database(url)
    # Alembic's async env calls asyncio.run(), so it needs a thread without a running loop.
    await anyio.to_thread.run_sync(_run_migrations, url)
    test_engine = create_async_engine(url)
    yield test_engine
    await test_engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as conn:
        transaction = await conn.begin()
        # Service-level commits become savepoints, so the outer rollback undoes everything.
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def api(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def create_user(db_session: AsyncSession) -> CreateUser:
    async def _create(
        email: str = "rep@tawasol.test",
        *,
        role: Role = Role.SALES_REP,
        password: str | None = TEST_PASSWORD,
        is_active: bool = True,
        full_name: str = "Test User",
    ) -> User:
        user = User(
            email=email,
            full_name=full_name,
            role=role,
            password_hash=hash_password(password) if password is not None else None,
            is_active=is_active,
        )
        db_session.add(user)
        await db_session.flush()
        return user

    return _create


async def login(api: AsyncClient, email: str, password: str = TEST_PASSWORD) -> str:
    response = await api.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def queued_jobs(session: AsyncSession, task_name: str | None = None) -> list[dict[str, Any]]:
    """Jobs waiting in procrastinate's queue, as seen inside the test's transaction."""
    query = "SELECT task_name, args FROM procrastinate_jobs WHERE status = 'todo'"
    params: dict[str, Any] = {}
    if task_name is not None:
        query += " AND task_name = :name"
        params["name"] = task_name
    rows = await session.execute(text(query + " ORDER BY id"), params)
    return [{"task": row.task_name, "args": row.args} for row in rows]
