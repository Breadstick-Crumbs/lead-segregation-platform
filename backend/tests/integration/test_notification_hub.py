"""The LISTEN/NOTIFY fan-out against a real database. NOTIFY is delivered only on commit, so
this test commits for real and cleans up after itself."""

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.integrations.pg_listener import NotificationHub
from app.models import Notification, User
from app.models.enums import NotificationKind, Role
from app.repositories.notifications import NOTIFY_CHANNEL, NotificationRepository

pytestmark = pytest.mark.integration

EMAIL = "hub-listener@hub.test"


@pytest.fixture
async def hub(engine: AsyncEngine) -> AsyncIterator[NotificationHub]:
    conninfo = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    listener = NotificationHub(conninfo, NOTIFY_CHANNEL)
    await listener.start()
    await asyncio.wait_for(listener.listening.wait(), timeout=10)
    yield listener
    await listener.stop()


async def test_a_committed_notification_reaches_the_users_open_streams(
    engine: AsyncEngine, hub: NotificationHub
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = User(email=EMAIL, full_name="Hub", role=Role.SALES_REP)
        session.add(user)
        await session.commit()
    try:
        async with hub.subscribe(user.id) as mine:
            async with sessions() as session:
                repo = NotificationRepository(session)
                await repo.add(
                    Notification(
                        user_id=user.id,
                        kind=NotificationKind.ESCALATION,
                        title="Rolled back",
                        body="",
                    )
                )
                await session.rollback()  # never committed: nobody hears about it
                await repo.add(
                    Notification(
                        user_id=user.id, kind=NotificationKind.ESCALATION, title="Critical", body=""
                    )
                )
                await session.commit()

            payload = json.loads(await asyncio.wait_for(mine.get(), timeout=10))

        assert payload["title"] == "Critical"
        assert payload["user_id"] == str(user.id)
        assert mine.empty()
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.email == EMAIL))
            await session.commit()


def test_other_users_and_bad_payloads_are_not_delivered() -> None:
    hub = NotificationHub("postgresql://unused", NOTIFY_CHANNEL)

    hub.dispatch("not json")
    hub.dispatch(json.dumps({"user_id": "00000000-0000-0000-0000-000000000001"}))  # no stream
