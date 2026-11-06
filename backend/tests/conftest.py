from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.runtime import use_selector_event_loop_on_windows
from app.main import create_app

# Must run before pytest-asyncio creates the event loop (psycopg needs the selector loop).
use_selector_event_loop_on_windows()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
