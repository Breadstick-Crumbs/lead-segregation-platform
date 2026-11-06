"""Enqueue jobs inside the caller's database transaction."""

from typing import Any

from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import raw_connection
from app.workers.app import app


async def enqueue(
    session: AsyncSession,
    task_name: str,
    *,
    queueing_lock: str | None = None,
    lock: str | None = None,
    schedule_in: dict[str, int] | None = None,
    **kwargs: Any,
) -> bool:
    """Insert a job on the session's connection, so it becomes visible only if the session
    commits. Returns False when `queueing_lock` finds an identical job already waiting.

    `queueing_lock` stops duplicates from *queueing*; `lock` stops jobs that share it from
    *running* at the same time (e.g. two workers polling one mailbox)."""
    options: dict[str, Any] = {"lock": lock, "schedule_in": schedule_in}
    if queueing_lock is None:
        await _defer(session, task_name, kwargs, queueing_lock=None, **options)
        return True
    # A lock clash is a unique violation, which would abort the caller's whole transaction;
    # a savepoint confines it to this insert.
    try:
        async with session.begin_nested():
            await _defer(session, task_name, kwargs, queueing_lock=queueing_lock, **options)
    except AlreadyEnqueued:
        return False
    return True


async def _defer(
    session: AsyncSession,
    task_name: str,
    kwargs: dict[str, Any],
    *,
    queueing_lock: str | None,
    lock: str | None,
    schedule_in: dict[str, int] | None,
) -> None:
    connection = await raw_connection(session)
    deferrer = app.configure_task(
        name=task_name,
        connection=connection,
        queueing_lock=queueing_lock,
        lock=lock,
        schedule_in=schedule_in,
    )
    await deferrer.defer_async(**kwargs)
