"""Leads for the dashboard: list and filter, stats, detail, similar leads, and actions."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.repositories.lead_queries import LeadRow
from app.schemas.lead import (
    ContactRead,
    EventRead,
    LeadDetail,
    LeadFilters,
    LeadListItem,
    LeadPage,
    LeadRef,
    LeadStats,
    LeadUpdate,
    MessageRead,
    NoteCreate,
    ReplyCreate,
    ScoreRead,
    SimilarLeadRead,
)
from app.schemas.user import UserSummary
from app.services.leads import LeadService, display_name

router = APIRouter(prefix="/leads", tags=["leads"])


def _item(row: LeadRow) -> LeadListItem:
    lead = row.lead
    return LeadListItem(
        id=lead.id,
        source=lead.source,
        category=lead.category,
        score=lead.score,
        priority=lead.priority,
        status=lead.status,
        processing_state=lead.processing_state,
        summary=lead.summary,
        language=lead.language,
        contact=ContactRead.model_validate(row.contact),
        owner=UserSummary.model_validate(row.owner) if row.owner else None,
        created_at=lead.created_at,
        last_message_at=row.last_message_at,
        sla_breached=row.sla_breached,
    )


@router.get("")
async def list_leads(
    filters: Annotated[LeadFilters, Query()],
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> LeadPage:
    """Filter by source, category, priority, status, owner (`me`, `unassigned` or a user
    id), score range and free text; sort by newest, score or latest activity."""
    rows, total = await LeadService(session, settings).page(filters, viewer=user)
    return LeadPage(
        items=[_item(row) for row in rows],
        total=total,
        page=filters.page,
        page_size=filters.page_size,
    )


@router.get("/stats")
async def lead_stats(_: CurrentUser, session: SessionDep, settings: SettingsDep) -> LeadStats:
    return LeadStats.model_validate(await LeadService(session, settings).stats())


async def _detail(service: LeadService, lead_id: uuid.UUID) -> LeadDetail:
    data = await service.detail(lead_id)
    duplicate = None
    if data.duplicate is not None:
        dup_lead, dup_contact = data.duplicate
        duplicate = LeadRef(
            id=dup_lead.id, display_name=display_name(dup_contact), status=dup_lead.status
        )
    return LeadDetail(
        **_item(data.row).model_dump(),
        processing_error=data.row.lead.processing_error,
        first_response_at=data.row.lead.first_response_at,
        converted_at=data.row.lead.converted_at,
        latest_score=ScoreRead.model_validate(data.score) if data.score else None,
        messages=[MessageRead.model_validate(m) for m in data.messages],
        events=[EventRead.model_validate(e) for e in data.events],
        possible_duplicate_of=duplicate,
        can_reply=data.reply_blocked_reason is None,
        reply_blocked_reason=data.reply_blocked_reason,
    )


@router.get("/{lead_id}")
async def get_lead(
    lead_id: uuid.UUID, _: CurrentUser, session: SessionDep, settings: SettingsDep
) -> LeadDetail:
    return await _detail(LeadService(session, settings), lead_id)


@router.get("/{lead_id}/similar")
async def similar_leads(
    lead_id: uuid.UUID, _: CurrentUser, session: SessionDep, settings: SettingsDep
) -> list[SimilarLeadRead]:
    """Nearest past leads by meaning (pgvector), most similar first."""
    return [
        SimilarLeadRead(
            id=s.lead.id,
            display_name=display_name(s.contact),
            category=s.lead.category,
            score=s.lead.score,
            status=s.lead.status,
            summary=s.lead.summary,
            similarity=round(s.similarity, 3),
            created_at=s.lead.created_at,
        )
        for s in await LeadService(session, settings).similar(lead_id)
    ]


@router.patch("/{lead_id}")
async def update_lead(
    lead_id: uuid.UUID,
    body: LeadUpdate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> LeadDetail:
    """Change status and/or owner. Moving a lead out of `new` counts as its first response."""
    service = LeadService(session, settings)
    await service.update(
        lead_id,
        actor=user,
        status=body.status,
        owner_id=body.owner_id,
        owner_given="owner_id" in body.model_fields_set,
    )
    return await _detail(service, lead_id)


@router.post("/{lead_id}/reply", status_code=status.HTTP_202_ACCEPTED)
async def reply_to_lead(
    lead_id: uuid.UUID,
    body: ReplyCreate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> MessageRead:
    """Queue a reply on the lead's channel (email threaded, or WhatsApp within 24 hours).
    It is sent by the worker; follow `delivery_status` on the returned message."""
    message = await LeadService(session, settings).reply(lead_id, body.body, actor=user)
    return MessageRead.model_validate(message)


@router.post("/{lead_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_lead_note(
    lead_id: uuid.UUID,
    body: NoteCreate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> EventRead:
    event = await LeadService(session, settings).add_note(lead_id, body.body, actor=user)
    return EventRead.model_validate(event)
