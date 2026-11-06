from collections.abc import AsyncIterator
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.db.session import get_session
from app.main import create_app


class _UnreachableSession:
    async def execute(self, *_: Any, **__: Any) -> None:
        raise OSError("connection refused")


async def _unreachable_session() -> AsyncIterator[_UnreachableSession]:
    yield _UnreachableSession()


async def test_readiness_reports_503_when_database_is_unreachable() -> None:
    app = create_app()
    app.dependency_overrides[get_session] = _unreachable_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
