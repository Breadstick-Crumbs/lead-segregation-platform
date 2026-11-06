"""Async engine and session factory."""

from collections.abc import AsyncIterator
from typing import cast

import psycopg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

engine = create_async_engine(str(get_settings().database_url), pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def raw_connection(session: AsyncSession) -> psycopg.AsyncConnection[object]:
    """The psycopg connection behind the session's current transaction.

    Lets Procrastinate insert a job in the same transaction as the rows it refers to.
    """
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    return cast(psycopg.AsyncConnection[object], raw.driver_connection)
