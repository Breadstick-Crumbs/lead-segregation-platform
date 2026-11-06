from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import SecretBox
from app.db.session import get_session
from app.integrations.imap import FetchedEmail, FetchResult, ImapCredentials
from app.main import create_app
from app.models import Lead, Mailbox
from app.models.enums import Role
from app.schemas.mailbox import MailboxCreate
from app.services.ingestion import Outcome
from app.services.mailboxes import MailboxService
from app.workers import names
from tests.fakes.messages import raw_email
from tests.integration.conftest import CreateUser, bearer, login, queued_jobs

pytestmark = pytest.mark.integration

NEW_MAILBOX = {
    "name": "Sales inbox",
    "email_address": "Sales@Example.com",
    "imap_host": "imap.example.com",
    "smtp_host": "smtp.example.com",
    "username": "sales@example.com",
    "password": "imap-app-password",
}


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={"secrets_encryption_key": SecretStr(Fernet.generate_key().decode())}
    )


@pytest.fixture
async def admin_api(
    db_session: AsyncSession, settings: Settings, create_user: CreateUser
) -> AsyncIterator[tuple[AsyncClient, dict[str, str]]]:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    await create_user("admin@tawasol.test", role=Role.ADMIN)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c, bearer(await login(c, "admin@tawasol.test"))


@dataclass
class FakeReader:
    """Stands in for the IMAP server: a folder with UIDs and a UIDVALIDITY."""

    uidvalidity: int = 1
    emails: list[FetchedEmail] = field(default_factory=list)
    error: Exception | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def fetch_new(
        self,
        credentials: ImapCredentials,
        *,
        last_uid: int | None,
        uidvalidity: int | None,
        since: date,
        limit: int,
    ) -> FetchResult:
        self.calls.append(
            {"password": credentials.password, "last_uid": last_uid, "uidvalidity": uidvalidity}
        )
        if self.error:
            raise self.error
        resume = last_uid is not None and uidvalidity == self.uidvalidity
        new = [e for e in self.emails if not resume or e.uid > (last_uid or 0)]
        return FetchResult(uidvalidity=self.uidvalidity, emails=new[:limit])


async def _mailbox(db_session: AsyncSession, settings: Settings) -> Mailbox:
    return await MailboxService(db_session, settings).create(
        MailboxCreate.model_validate(NEW_MAILBOX)
    )


async def test_admin_adds_a_mailbox_and_the_password_is_encrypted(
    admin_api: tuple[AsyncClient, dict[str, str]], db_session: AsyncSession, settings: Settings
) -> None:
    api, auth = admin_api

    response = await api.post("/api/v1/mailboxes", headers=auth, json=NEW_MAILBOX)

    assert response.status_code == 201
    body = response.json()
    assert body["email_address"] == "sales@example.com"
    assert "password" not in body
    assert "encrypted_secret" not in body
    stored = await db_session.scalar(select(Mailbox))
    assert stored is not None
    assert "imap-app-password" not in stored.encrypted_secret
    assert SecretBox(settings).decrypt(stored.encrypted_secret) == "imap-app-password"


async def test_mailboxes_are_admin_only(
    admin_api: tuple[AsyncClient, dict[str, str]], create_user: CreateUser
) -> None:
    api, _ = admin_api
    await create_user("rep@tawasol.test")
    rep = bearer(await login(api, "rep@tawasol.test"))

    assert (await api.get("/api/v1/mailboxes", headers=rep)).status_code == 403
    assert (await api.post("/api/v1/mailboxes", headers=rep, json=NEW_MAILBOX)).status_code == 403


async def test_without_an_encryption_key_mailboxes_cannot_be_added(
    db_session: AsyncSession, create_user: CreateUser
) -> None:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"secrets_encryption_key": None}
    )
    await create_user("admin@tawasol.test", role=Role.ADMIN)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        auth = bearer(await login(c, "admin@tawasol.test"))
        response = await c.post("/api/v1/mailboxes", headers=auth, json=NEW_MAILBOX)

    assert response.status_code == 503


async def test_poll_now_queues_one_job_even_if_clicked_twice(
    admin_api: tuple[AsyncClient, dict[str, str]], db_session: AsyncSession, settings: Settings
) -> None:
    api, auth = admin_api
    mailbox = await _mailbox(db_session, settings)

    first = await api.post(f"/api/v1/mailboxes/{mailbox.id}/poll", headers=auth)
    second = await api.post(f"/api/v1/mailboxes/{mailbox.id}/poll", headers=auth)

    assert (first.json(), second.json()) == ({"queued": True}, {"queued": False})
    assert len(await queued_jobs(db_session, names.POLL_MAILBOX)) == 1


async def test_polling_ingests_new_mail_and_skips_robots(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox = await _mailbox(db_session, settings)
    reader = FakeReader(
        emails=[
            FetchedEmail(1, raw_email(message_id="<a@gulfbuild.ae>")),
            FetchedEmail(
                2, raw_email(message_id="<b@x>", headers={"Auto-Submitted": "auto-replied"})
            ),
            FetchedEmail(
                3,
                raw_email(
                    sender="Omar Haddad <omar.haddad@gulfbuild.ae>",
                    message_id="<c@gulfbuild.ae>",
                    in_reply_to="<a@gulfbuild.ae>",
                    body="Any update?",
                ),
            ),
        ]
    )
    service = MailboxService(db_session, settings)

    counts = await service.poll(mailbox.id, reader)

    assert counts == {Outcome.NEW_LEAD: 1, Outcome.ATTACHED: 1}
    assert reader.calls[0]["password"] == "imap-app-password"
    await db_session.refresh(mailbox)
    assert (mailbox.last_uid, mailbox.uidvalidity, mailbox.last_error) == (3, 1, None)
    assert mailbox.last_polled_at is not None

    # The next poll resumes after UID 3 and finds nothing new.
    assert await service.poll(mailbox.id, reader) == {}
    assert reader.calls[1]["last_uid"] == 3


async def test_a_uidvalidity_change_rereads_by_date_and_dedupes(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox = await _mailbox(db_session, settings)
    reader = FakeReader(emails=[FetchedEmail(5, raw_email(message_id="<a@gulfbuild.ae>"))])
    service = MailboxService(db_session, settings)
    await service.poll(mailbox.id, reader)

    # The server rebuilt the folder: same mail, new UIDs.
    reader.uidvalidity, reader.emails = (
        2,
        [FetchedEmail(1, raw_email(message_id="<a@gulfbuild.ae>"))],
    )
    counts = await service.poll(mailbox.id, reader)

    assert counts == {Outcome.DUPLICATE: 1}
    await db_session.refresh(mailbox)
    assert (mailbox.uidvalidity, mailbox.last_uid) == (2, 1)
    assert await db_session.scalar(select(func.count()).select_from(Lead)) == 1


async def test_an_imap_failure_is_recorded_on_the_mailbox(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox = await _mailbox(db_session, settings)

    await MailboxService(db_session, settings).poll(
        mailbox.id, FakeReader(error=OSError("connection refused"))
    )

    await db_session.refresh(mailbox)
    assert mailbox.last_error == "IMAP error: connection refused"
    assert mailbox.last_uid is None


async def test_inactive_mailboxes_are_not_polled(
    db_session: AsyncSession, settings: Settings
) -> None:
    mailbox = await _mailbox(db_session, settings)
    mailbox.is_active = False
    await db_session.commit()
    reader = FakeReader(emails=[FetchedEmail(1, raw_email())])

    assert await MailboxService(db_session, settings).poll(mailbox.id, reader) == {}
    assert reader.calls == []
