"""The signed-in user's notifications, and a live stream of new ones (Server-Sent Events)."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette import EventSourceResponse

from app.api.deps import CurrentUser, SessionDep, SettingsDep, StreamClaims
from app.integrations.pg_listener import RESYNC, NotificationHub
from app.schemas.notification import MarkedRead, NotificationPage, NotificationRead
from app.services.notifications import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])

PING_SECONDS = 15


@router.get("")
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    *,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    before: datetime | None = None,
) -> NotificationPage:
    """Newest first; page with `before` (the `created_at` of the last item)."""
    items, unread = await NotificationService(session, settings).page(
        user.id, unread_only=unread_only, limit=limit, before=before
    )
    return NotificationPage(
        items=[NotificationRead.model_validate(n) for n in items], unread=unread
    )


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> NotificationRead:
    notification = await NotificationService(session, settings).mark_read(user.id, notification_id)
    return NotificationRead.model_validate(notification)


@router.post("/read-all")
async def mark_all_notifications_read(
    user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> MarkedRead:
    return MarkedRead(updated=await NotificationService(session, settings).mark_all_read(user.id))


def get_hub(request: Request) -> NotificationHub:
    hub: NotificationHub = request.app.state.notification_hub
    return hub


async def notification_events(
    queue: asyncio.Queue[str], *, until: datetime
) -> AsyncIterator[dict[str, Any]]:
    """`ready`, then each notification, until the access token expires. Ending there means a
    deactivated user's stream doesn't outlive their token."""
    yield {"event": "ready", "data": "{}"}
    while (remaining := (until - datetime.now(UTC)).total_seconds()) > 0:
        with contextlib.suppress(TimeoutError):
            payload = await asyncio.wait_for(queue.get(), timeout=remaining)
            # After a listener reconnect, missed events can't be replayed: reload the list.
            event = "resync" if payload == RESYNC else "notification"
            yield {"event": event, "data": payload}
    yield {"event": "expired", "data": "{}"}


@router.get("/stream", response_class=EventSourceResponse)
async def stream_notifications(
    claims: StreamClaims,
    hub: Annotated[NotificationHub, Depends(get_hub)],
) -> EventSourceResponse:
    """Server-Sent Events: `ready`, then `notification` for each new one, then `expired`
    when the access token runs out. Send the token as a Bearer header (use a fetch-based
    client; EventSource can't set headers). On `expired` or a dropped connection, refresh
    the token, reconnect and reload the list: events missed while disconnected aren't
    replayed."""

    async def events() -> AsyncIterator[dict[str, Any]]:
        async with hub.subscribe(claims.user_id) as queue:
            async for event in notification_events(queue, until=claims.expires_at):
                yield event

    return EventSourceResponse(events(), ping=PING_SECONDS)
