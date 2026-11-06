"""The reporting screen's numbers for a period (docs/REPORTING.md).

The period is the last N days up to now. Days are bucketed in the reporting time zone
(Asia/Dubai by default), so "Tuesday" means a Tuesday for the sales team, not in UTC.
"""

import math
import statistics
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.enums import Channel, LeadStatus, Priority
from app.repositories.report_queries import ReportQueries, ResponseRow
from app.schemas.app_settings import ScoringThresholds
from app.schemas.report import (
    CategoryCount,
    Funnel,
    HighValue,
    HighValueLead,
    Report,
    ReportPeriod,
    ResponseTimes,
    SourceResponse,
    VolumeDay,
)
from app.services.app_settings import load_settings
from app.services.leads import display_name

URGENT = (Priority.HIGH, Priority.CRITICAL)
P90 = 0.9


class InvalidTimezoneError(AppError):
    status_code = 422
    code = "invalid_timezone"


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile, rounded to a minute's tenth."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(q * len(ordered)) - 1)], 1)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _sla_decided(row: ResponseRow, sla_minutes: int) -> bool:
    if row.minutes is not None:
        return True
    return row.age_minutes >= sla_minutes and row.status != LeadStatus.LOST


def response_times(rows: list[ResponseRow], sla_minutes: int) -> ResponseTimes:
    answered = [r.minutes for r in rows if r.minutes is not None]
    # The SLA counts urgent leads whose outcome is known: answered, or unanswered with the
    # window already over. A lead still inside its window can make it, so it isn't a miss
    # yet; one closed as lost without a response stopped the clock, as in the alerts.
    urgent = [r for r in rows if r.priority in URGENT and _sla_decided(r, sla_minutes)]
    within = [r for r in urgent if r.minutes is not None and r.minutes <= sla_minutes]
    by_source = []
    for source in Channel:
        minutes = [r.minutes for r in rows if r.source == source and r.minutes is not None]
        by_source.append(
            SourceResponse(source=source, responded=len(minutes), median_minutes=_median(minutes))
        )
    return ResponseTimes(
        responded=len(answered),
        unanswered=len(rows) - len(answered),
        median_minutes=_median(answered),
        p90_minutes=_percentile(answered, P90),
        sla_minutes=sla_minutes,
        urgent=len(urgent),
        urgent_within_sla=len(within),
        within_sla_rate=_rate(len(within), len(urgent)),
        by_source=by_source,
    )


class ReportService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def report(self, *, days: int, timezone: str | None = None) -> Report:
        zone_name = timezone or self._settings.reporting_timezone
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidTimezoneError(f"Unknown time zone {zone_name!r}") from exc
        end = datetime.now(UTC)
        # N whole local days ending today (today is partial): N bars, starting at midnight.
        first_day = end.astimezone(zone).date() - timedelta(days=days - 1)
        start = datetime.combine(first_day, time(0), zone).astimezone(UTC)
        queries = ReportQueries(self._session, start=start, end=end)
        if not await queries.timezone_exists(zone_name):
            raise InvalidTimezoneError(f"Unknown time zone {zone_name!r}")
        thresholds = await load_settings(self._session, ScoringThresholds)

        totals = await queries.totals()
        funnel = await queries.funnel()
        count, converted, top = await queries.high_value(thresholds.high_at)
        non_spam = totals["total"] - totals["spam"]
        return Report(
            period=ReportPeriod(start=start, end=end, days=days, timezone=zone_name),
            total=totals["total"],
            spam=totals["spam"],
            by_source={Channel.EMAIL: totals["email"], Channel.WHATSAPP: totals["whatsapp"]},
            auto_replies_sent=await queries.auto_replies_sent(),
            volume=self._volume(await queries.volume(zone_name), start, end, zone),
            categories=[
                CategoryCount(category=category, count=n)
                for category, n in await queries.categories()
            ],
            funnel=Funnel(**funnel, conversion_rate=_rate(funnel["converted"], funnel["created"])),
            response=response_times(await queries.response_rows(), thresholds.sla_minutes),
            high_value=HighValue(
                threshold=thresholds.high_at,
                count=count,
                converted=converted,
                conversion_rate=_rate(converted, count),
                share_of_leads=_rate(count, non_spam),
                top=[
                    HighValueLead(
                        id=t.lead.id,
                        display_name=display_name(t.contact),
                        score=t.lead.score or 0,
                        priority=t.lead.priority,
                        category=t.lead.category,
                        status=t.lead.status,
                        owner_name=t.owner_name,
                        created_at=t.lead.created_at,
                    )
                    for t in top
                ],
            ),
        )

    @staticmethod
    def _volume(
        counts: dict[tuple[date, Channel], int], start: datetime, end: datetime, zone: ZoneInfo
    ) -> list[VolumeDay]:
        """Every day of the period, including days with no leads (gaps would mislead)."""
        first, last = start.astimezone(zone).date(), end.astimezone(zone).date()
        days = []
        day = first
        while day <= last:
            days.append(
                VolumeDay(
                    date=day,
                    email=counts.get((day, Channel.EMAIL), 0),
                    whatsapp=counts.get((day, Channel.WHATSAPP), 0),
                )
            )
            day += timedelta(days=1)
        return days
