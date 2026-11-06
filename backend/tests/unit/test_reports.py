from app.models.enums import Channel, LeadStatus, Priority
from app.repositories.report_queries import ResponseRow
from app.services.reports import response_times


def _row(
    minutes: float | None,
    source: Channel = Channel.EMAIL,
    priority: Priority | None = None,
    *,
    age: float = 1000,
    status: LeadStatus = LeadStatus.NEW,
) -> ResponseRow:
    return ResponseRow(
        source=source, priority=priority, status=status, minutes=minutes, age_minutes=age
    )


def test_median_p90_and_unanswered() -> None:
    rows = [_row(m) for m in (5, 10, 20, 30, 40, 50, 60, 70, 80, 600)] + [_row(None)]

    result = response_times(rows, sla_minutes=60)

    assert (result.responded, result.unanswered) == (10, 1)
    assert result.median_minutes == 45.0
    assert result.p90_minutes == 80.0  # nearest rank: the 9th of 10


def test_sla_counts_only_urgent_leads_and_unanswered_ones_miss_it() -> None:
    rows = [
        _row(30, priority=Priority.HIGH),
        _row(90, priority=Priority.CRITICAL),
        _row(None, priority=Priority.HIGH),  # waited past the SLA: missed
        _row(5, priority=Priority.LOW),  # not urgent: not in the SLA figure
    ]

    result = response_times(rows, sla_minutes=60)

    assert (result.urgent, result.urgent_within_sla, result.within_sla_rate) == (3, 1, 0.333)


def test_a_lead_still_inside_its_window_is_not_a_miss_yet() -> None:
    # Regression: a critical lead that arrived 5 minutes ago counted against the rate.
    rows = [
        _row(10, priority=Priority.CRITICAL),
        _row(None, priority=Priority.CRITICAL, age=5),
        _row(None, priority=Priority.HIGH, status=LeadStatus.LOST),  # closed: clock stopped
    ]

    result = response_times(rows, sla_minutes=60)

    assert (result.urgent, result.urgent_within_sla, result.within_sla_rate) == (1, 1, 1.0)


def test_by_source_and_empty_periods() -> None:
    rows = [_row(10, Channel.WHATSAPP), _row(20, Channel.WHATSAPP), _row(None, Channel.EMAIL)]

    result = response_times(rows, sla_minutes=60)
    empty = response_times([], sla_minutes=60)

    by_source = {s.source: (s.responded, s.median_minutes) for s in result.by_source}
    assert by_source == {Channel.EMAIL: (0, None), Channel.WHATSAPP: (2, 15.0)}
    assert (empty.median_minutes, empty.p90_minutes, empty.within_sla_rate) == (None, None, None)
