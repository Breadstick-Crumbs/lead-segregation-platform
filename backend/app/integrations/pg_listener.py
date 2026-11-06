"""Fan-out of new notifications to open browser streams, through Postgres LISTEN/NOTIFY.

Each API process keeps one dedicated connection listening on the channel. The worker (or any
process) inserts a notification and calls `pg_notify` in the same transaction, so a stream
hears about it only once it is committed. No broker is needed, and it works across any number
of API processes.

NOTIFY is fire-and-forget: a stream that was disconnected misses what happened meanwhile. The
dashboard therefore reloads the list when it (re)connects; the stream only adds liveliness.
"""

import asyncio
import contextlib
import json
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator

import psycopg

from app.core.logging import get_logger

logger = get_logger(__name__)

QUEUE_SIZE = 100  # per stream; a stream this far behind just drops events
RECONNECT_MAX_SECONDS = 30.0
# Sent to every open stream after the listener reconnects: events may have been missed.
RESYNC = '{"type": "resync"}'


class NotificationHub:
    def __init__(self, conninfo: str, channel: str) -> None:
        self._conninfo = conninfo
        self._channel = channel
        self._subscribers: defaultdict[uuid.UUID, set[asyncio.Queue[str]]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None
        self.listening = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="notification-hub")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @contextlib.asynccontextmanager
    async def subscribe(self, user_id: uuid.UUID) -> AsyncIterator[asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers[user_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[user_id].discard(queue)
            if not self._subscribers[user_id]:
                del self._subscribers[user_id]

    def dispatch(self, payload: str) -> None:
        """Hand one NOTIFY payload to the recipient's open streams."""
        try:
            user_id = uuid.UUID(json.loads(payload)["user_id"])
        except (ValueError, KeyError, TypeError):
            logger.warning("notification_payload_invalid")
            return
        for queue in self._subscribers.get(user_id, ()):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(payload)

    def resync_all(self) -> None:
        for queues in self._subscribers.values():
            for queue in queues:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(RESYNC)

    async def _run(self) -> None:
        delay = 1.0
        connected_before = False
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._conninfo, autocommit=True
                ) as connection:
                    await connection.execute(f"LISTEN {self._channel}")
                    self.listening.set()
                    delay = 1.0
                    if connected_before:
                        self.resync_all()  # NOTIFYs sent while we were away are gone
                    connected_before = True
                    logger.info("notification_hub_listening", channel=self._channel)
                    async for notify in connection.notifies():
                        self.dispatch(notify.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # database restart, network blip: reconnect with backoff
                self.listening.clear()
                logger.warning("notification_hub_disconnected", error=repr(exc), retry_in=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_SECONDS)
