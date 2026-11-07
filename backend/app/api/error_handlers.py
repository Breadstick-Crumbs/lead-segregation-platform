"""Maps domain exceptions to HTTP responses, in one place."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError, AuthenticationError, RateLimitedError


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):  # registered only for AppError; narrows the type
        raise exc
    headers: dict[str, str] | None = None
    if isinstance(exc, AuthenticationError):
        headers = {"WWW-Authenticate": "Bearer"}
    elif isinstance(exc, RateLimitedError):
        headers = {"Retry-After": str(exc.retry_after_seconds)}
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
