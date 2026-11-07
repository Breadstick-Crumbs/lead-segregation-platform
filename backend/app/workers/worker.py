"""The worker process's entry point: the Procrastinate app with logging and error reporting
set up, which the API (which only enqueues) doesn't want at import time.

    uv run procrastinate --app=app.workers.worker.app worker
"""

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.observability import init_sentry
from app.workers.app import app

_settings = get_settings()
configure_logging(_settings.log_level, json_logs=_settings.is_production)
init_sentry(_settings, "worker")

__all__ = ["app"]
