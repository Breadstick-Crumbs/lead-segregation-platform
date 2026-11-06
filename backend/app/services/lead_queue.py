"""Queueing the AI pipeline for a lead. One waiting job per lead: the queueing lock makes a
second enqueue a no-op while one is still waiting."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.leads import LeadRepository
from app.workers import names
from app.workers.queue import enqueue

# A lead updated more recently than this may still have its job on the way.
SWEEP_GRACE = timedelta(minutes=10)
SWEEP_BATCH = 200


async def enqueue_process_lead(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    """Queue the pipeline in the caller's transaction; False if a job is already waiting."""
    return await enqueue(
        session,
        names.PROCESS_LEAD,
        queueing_lock=_lock(lead_id),
        lead_id=str(lead_id),
    )


def _lock(lead_id: uuid.UUID) -> str:
    return f"process_lead:{lead_id}"


async def has_waiting_job(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    """A newer `process_lead` job is queued for this lead (it will see everything)."""
    found = await session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM procrastinate_jobs"
            " WHERE queueing_lock = :lock AND status = 'todo')"
        ),
        {"lock": _lock(lead_id)},
    )
    return bool(found)


async def sweep_pending_leads(session: AsyncSession) -> int:
    """Queue every open lead stuck at `pending` (skipping ones still being transcribed).
    Commits; returns how many jobs were added."""
    before = datetime.now(UTC) - SWEEP_GRACE
    queued = 0
    for lead_id in await LeadRepository(session).stale_pending_ids(
        before=before, limit=SWEEP_BATCH
    ):
        queued += await enqueue_process_lead(session, lead_id)
    await session.commit()
    return queued
