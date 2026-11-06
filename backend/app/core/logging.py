"""structlog setup: JSON lines in production, readable console output in development."""

import io
import logging
import sys

import structlog


def _make_stdout_unicode_safe() -> None:
    # Windows consoles default to cp1252; an unencodable character in a log line (e.g. in a
    # traceback) would otherwise raise inside the logger and crash the request being logged.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")


def configure_logging(level: str, *, json_logs: bool) -> None:
    _make_stdout_unicode_safe()
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)
    )
    if json_logs:
        shared_processors.append(structlog.processors.format_exc_info)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
