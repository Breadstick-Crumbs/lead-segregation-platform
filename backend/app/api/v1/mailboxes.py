import uuid

from fastapi import APIRouter, status

from app.api.deps import AdminUser, SessionDep, SettingsDep
from app.schemas.mailbox import MailboxCreate, MailboxRead, MailboxUpdate, PollRequested
from app.services.mailboxes import MailboxService

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])


@router.get("")
async def list_mailboxes(
    _: AdminUser, session: SessionDep, settings: SettingsDep
) -> list[MailboxRead]:
    mailboxes = await MailboxService(session, settings).list_mailboxes()
    return [MailboxRead.model_validate(m) for m in mailboxes]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mailbox(
    body: MailboxCreate, _: AdminUser, session: SessionDep, settings: SettingsDep
) -> MailboxRead:
    return MailboxRead.model_validate(await MailboxService(session, settings).create(body))


@router.patch("/{mailbox_id}")
async def update_mailbox(
    mailbox_id: uuid.UUID,
    body: MailboxUpdate,
    _: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
) -> MailboxRead:
    mailbox = await MailboxService(session, settings).update(mailbox_id, body)
    return MailboxRead.model_validate(mailbox)


@router.post("/{mailbox_id}/poll", status_code=status.HTTP_202_ACCEPTED)
async def poll_mailbox_now(
    mailbox_id: uuid.UUID, _: AdminUser, session: SessionDep, settings: SettingsDep
) -> PollRequested:
    """Check the inbox now instead of waiting for the next minute."""
    queued = await MailboxService(session, settings).request_poll(mailbox_id)
    return PollRequested(queued=queued)
