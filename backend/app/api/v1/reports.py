"""Reporting: volume by source, categories, the funnel, response times, high-value leads."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.schemas.report import Report
from app.services.reports import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
async def lead_report(
    _: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    timezone: Annotated[str | None, Query(max_length=64)] = None,
) -> Report:
    """Leads opened in the last `days` days. Daily volume is bucketed in `timezone` (an IANA
    name; defaults to REPORTING_TIMEZONE)."""
    return await ReportService(session, settings).report(days=days, timezone=timezone)
