import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base

pytestmark = pytest.mark.integration


async def test_migrations_match_the_models(engine: AsyncEngine) -> None:
    """A model change without a migration (or a hand-edited migration) fails here."""

    def _diff(conn: Connection) -> list[object]:
        context = MigrationContext.configure(conn)
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as conn:
        diff = await conn.run_sync(_diff)

    assert diff == []
