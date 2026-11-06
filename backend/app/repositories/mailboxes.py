import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Mailbox
from app.models.enums import MailboxAuthType


class MailboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, mailbox_id: uuid.UUID) -> Mailbox | None:
        return await self._session.get(Mailbox, mailbox_id)

    async def list_all(self) -> Sequence[Mailbox]:
        return (await self._session.scalars(select(Mailbox).order_by(Mailbox.name))).all()

    async def active_ids(self) -> Sequence[uuid.UUID]:
        return (await self._session.scalars(select(Mailbox.id).where(Mailbox.is_active))).all()

    async def first_sending(self) -> Mailbox | None:
        """The oldest active mailbox we can send from (password auth)."""
        mailbox: Mailbox | None = await self._session.scalar(
            select(Mailbox)
            .where(Mailbox.is_active, Mailbox.auth_type == MailboxAuthType.PASSWORD)
            .order_by(Mailbox.created_at)
            .limit(1)
        )
        return mailbox

    def add(self, mailbox: Mailbox) -> None:
        self._session.add(mailbox)
