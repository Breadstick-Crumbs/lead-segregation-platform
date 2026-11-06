"""FastAPI application factory."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from sqlalchemy.engine import make_url

from app.api.error_handlers import register_error_handlers
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.integrations.pg_listener import NotificationHub
from app.repositories.notifications import NOTIFY_CHANNEL


def _operation_id(route: APIRoute) -> str:
    # Stable, readable operation IDs give clean names in the generated frontend client.
    return route.name


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.is_production)
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)

    conninfo = make_url(str(settings.database_url)).set(drivername="postgresql")
    hub = NotificationHub(conninfo.render_as_string(hide_password=False), NOTIFY_CHANNEL)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await hub.start()  # one LISTEN connection per process, for notification streams
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        version="0.1.0",
        generate_unique_id_function=_operation_id,
        docs_url=None if settings.is_production else "/docs",
    )
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
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.state.notification_hub = hub
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
