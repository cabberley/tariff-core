"""Timezone-aware matching of tariff windows and seasons."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tariff_core.models import HolidayTreatment, PlanVersion, Schedule


def to_local(plan: PlanVersion, when: datetime) -> datetime:
    """Convert an aware instant to plan time so schedules follow the tariff's clock."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("when must be timezone-aware")
    if plan.timezone is None:
        raise ValueError("plan timezone is required for rate evaluation")
    return when.astimezone(ZoneInfo(plan.timezone))


def season_at(plan: PlanVersion, when: datetime | date) -> str | None:
    """Return the season containing a local date, including wrapping seasons."""
    local_date = to_local(plan, when).date() if isinstance(when, datetime) else when
    month_day = (local_date.month, local_date.day)
    for name, season in plan.seasons.items():
        start = (season.from_.month, season.from_.day)
        end = (season.to.month, season.to.day)
        if start == end or (
            (start <= month_day < end) if start < end else (month_day >= start or month_day < end)
        ):
            return name
    return None


def holiday_dates(plan: PlanVersion, start: date, end: date) -> frozenset[date]:
    """Get local holidays in a date range when the plan asks to treat them specially."""
    holidays = plan.holidays
    if (
        holidays is None
        or holidays.treatment is not HolidayTreatment.AS_WEEKEND
        or holidays.calendar is None
    ):
        return frozenset()
    try:
        import holidays as holiday_library
    except ImportError as exc:
        raise RuntimeError(
            "install tariff-core[holidays] to evaluate this plan's holiday treatment"
        ) from exc

    country, _, subdivision = holidays.calendar.partition("-")
    years = range(start.year, end.year + 1)
    calendar = holiday_library.country_holidays(country, subdiv=subdivision or None, years=years)
    result: set[date] = set()
    current = start
    while current <= end:
        if current in calendar:
            result.add(current)
        current += timedelta(days=1)
    return frozenset(result)


def _weekday(day: date, holiday_dates: frozenset[date], treatment: HolidayTreatment) -> int:
    if treatment is HolidayTreatment.AS_WEEKEND and day in holiday_dates:
        return 5
    return day.weekday()


def matches_schedule(
    schedule: Schedule,
    when: datetime,
    *,
    holiday_dates: frozenset[date] = frozenset(),
    holiday_treatment: HolidayTreatment = HolidayTreatment.NONE,
) -> bool:
    """Match local wall time, preserving the starting day's ownership for midnight wraps."""
    current_time = when.timetz().replace(tzinfo=None)
    current_day = when.date()
    for window in schedule:
        start, end = window.time
        current_weekday = _weekday(current_day, holiday_dates, holiday_treatment)
        if start == end:
            if current_weekday in window.days:
                return True
        elif start < end:
            if start <= current_time < end and current_weekday in window.days:
                return True
        elif current_time >= start and current_weekday in window.days:
            return True
        elif current_time < end:
            previous_day = current_day - timedelta(days=1)
            previous_weekday = _weekday(previous_day, holiday_dates, holiday_treatment)
            if previous_weekday in window.days:
                return True
    return False
