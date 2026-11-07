import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


async def test_liveness_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_responses_carry_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"


async def test_unknown_route_is_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404


@pytest.mark.parametrize("supplied", ["x" * 65, "has spaces", "a/b<script>", ""])
async def test_odd_request_ids_are_replaced(client: AsyncClient, supplied: str) -> None:
    response = await client.get("/api/v1/health/live", headers={"X-Request-ID": supplied})

    assert response.headers["X-Request-ID"] != supplied
    assert len(response.headers["X-Request-ID"]) == 32


async def test_production_serves_no_api_schema_or_docs() -> None:
    settings = Settings(environment="production", jwt_secret="p" * 40)
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        statuses = [(await client.get(p)).status_code for p in ("/docs", "/redoc", "/openapi.json")]

    assert statuses == [404, 404, 404]
