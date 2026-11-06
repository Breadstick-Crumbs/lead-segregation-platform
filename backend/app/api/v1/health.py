from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


class HealthStatus(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """The process is up. Used by container liveness probes."""
    return {"status": "ok"}


@router.get("/health/ready", responses={503: {"model": HealthStatus}})
async def readiness(
    response: Response, session: Annotated[AsyncSession, Depends(get_session)]
) -> HealthStatus:
    """The app can serve traffic, which means the database is reachable."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        logger.warning("readiness_db_unavailable", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(status="degraded", database="unavailable")
    return HealthStatus(status="ok", database="ok")
