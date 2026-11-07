"""FastAPI application factory."""

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.error_handlers import register_error_handlers
from app.api.rate_limit import RateLimitMiddleware
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.observability import init_sentry
from app.core.rate_limit import RateLimiter
from app.db.session import get_session
from app.integrations.pg_listener import NotificationHub
from app.mcp_server.server import build_mcp_app
from app.repositories.notifications import NOTIFY_CHANNEL

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _operation_id(route: APIRoute) -> str:
    # Stable, readable operation IDs give clean names in the generated frontend client.
    return route.name


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.is_production)
    init_sentry(settings, "api")

    conninfo = make_url(str(settings.database_url)).set(drivername="postgresql")
    hub = NotificationHub(conninfo.render_as_string(hide_password=False), NOTIFY_CHANNEL)

    # The MCP app isn't FastAPI, so it can't take dependencies; these resolve through the
    # app's dependency overrides so tests swap the session and settings for MCP too.
    def mcp_session() -> AbstractAsyncContextManager[AsyncSession]:
        return asynccontextmanager(app.dependency_overrides.get(get_session, get_session))()

    def mcp_settings() -> Settings:
        override: Callable[[], Settings] = app.dependency_overrides.get(get_settings, get_settings)
        return override()

    mcp_app, mcp_sessions = build_mcp_app(settings, mcp_session, mcp_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await hub.start()  # one LISTEN connection per process, for notification streams
        try:
            async with mcp_sessions.run():
                yield
        finally:
            await hub.stop()

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        version="0.1.0",
        generate_unique_id_function=_operation_id,
        # The schema and docs map every endpoint; production serves neither.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    # Innermost, so a refused request still gets CORS headers and a request ID, but never
    # reaches routing or a database session.
    limiter = RateLimiter()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # A caller's ID is kept for tracing only if it looks like one; it lands in every log.
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.state.rate_limiter = limiter
    app.state.notification_hub = hub
    app.state.mcp_sessions = mcp_sessions  # tests run it without the full lifespan
    register_error_handlers(app)
    app.include_router(api_router)
    # Last, so the API's routes match first: /mcp and the OAuth endpoints (see PUBLIC_PATHS).
    app.mount("/", mcp_app)
    return app


app = create_app()
