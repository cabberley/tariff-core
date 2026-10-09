"""Point-in-time rate lookup and rate forecasting."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tariff_core.models import (
    Direction,
    HolidayTreatment,
    Period,
    PlanVersion,
    RateSource,
    Register,
    UsageComponent,
)
from tariff_core.schedule import holiday_dates, matches_schedule, season_at, to_local

_LOOKAHEAD = timedelta(days=8)
_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class RateInfo:
    rate: Decimal | None
    quantity_unit: str
    period: Period | None
    period_label: str | None
    season: str | None
    component_index: int
    next_change: datetime | None
    next_rate: Decimal | None
    next_period: Period | None
    is_dynamic: bool
    blocked: bool


@dataclass(frozen=True, slots=True)
class RateSlot:
    start: datetime
    end: datetime
    rate: Decimal | None
    period: Period | None


def select_component(
    plan: PlanVersion,
    when: datetime,
    *,
    register: Register | str = Register.GENERAL,
    direction: Direction | str = Direction.IMPORT,
    holidays: frozenset[date] = frozenset(),
) -> tuple[int, UsageComponent] | None:
    """Select the most specific usage component that applies at local wall time."""
    season = season_at(plan, when)
    holiday_treatment = (
        plan.holidays.treatment if plan.holidays is not None else HolidayTreatment.NONE
    )
    matches: list[tuple[tuple[bool, bool], int, UsageComponent]] = []
    for index, component in enumerate(plan.components):
        if not isinstance(component, UsageComponent):
            continue
        if component.stack:
            continue
        if component.register != register or component.direction != direction:
            continue
        if component.season is not None and component.season != season:
            continue
        if component.schedule is not None and not matches_schedule(
            component.schedule,
            when,
            holiday_dates=holidays,
            holiday_treatment=holiday_treatment,
        ):
            continue
        matches.append(
            ((component.schedule is not None, component.season is not None), index, component)
        )
    if not matches:
        return None
    _, index, component = max(matches, key=lambda match: match[0])
    return index, component


def _rate(component: UsageComponent, resolved: Mapping[str, Decimal] | None) -> Decimal | None:
    if component.blocks is not None:
        if not component.blocks.tiers:
            raise ValueError("block-priced usage component must have at least one tier")
        return component.blocks.tiers[0].rate
    if isinstance(component.rate, RateSource):
        if resolved is None or component.rate.entity not in resolved:
            return None
        value = resolved[component.rate.entity]
        if not isinstance(value, Decimal):
            raise TypeError("resolved dynamic rates must be Decimal values")
        if not value.is_finite():
            raise ValueError("resolved dynamic rates must be finite")
        return (
            value * component.rate.multiplier + component.rate.adder
        ) * component.rate.unit_scale
    return component.rate


def _signature(
    selected: tuple[int, UsageComponent] | None, resolved: Mapping[str, Decimal] | None
) -> tuple[int, Decimal | None, Period | None] | None:
    if selected is None:
        return None
    index, component = selected
    return index, _rate(component, resolved), component.period


def _next_minute(when: datetime) -> datetime:
    utc = when.astimezone(UTC)
    minute = utc.replace(second=0, microsecond=0)
    return minute + _MINUTE


def rate_at(
    plan: PlanVersion,
    when: datetime,
    *,
    register: Register | str = Register.GENERAL,
    direction: Direction | str = Direction.IMPORT,
    resolved: Mapping[str, Decimal] | None = None,
) -> RateInfo:
    """Return the applicable marginal tariff rate and the next schedule transition."""
    local = to_local(plan, when)
    current_season = season_at(plan, local)
    holiday_set = holiday_dates(plan, local.date(), (local + _LOOKAHEAD).date())
    selected = select_component(
        plan,
        local,
        register=register,
        direction=direction,
        holidays=holiday_set,
    )
    if selected is None:
        raise ValueError(
            f"no usage component applies for register {register!s} and direction {direction!s}"
        )

    component_index, component = selected
    current_signature = _signature(selected, resolved)
    next_change: datetime | None = None
    next_rate: Decimal | None = None
    next_period: Period | None = None
    candidate = _next_minute(when)
    for _ in range(int(_LOOKAHEAD.total_seconds() // 60)):
        candidate_local = to_local(plan, candidate)
        candidate_selected = select_component(
            plan,
            candidate_local,
            register=register,
            direction=direction,
            holidays=holiday_set,
        )
        if _signature(candidate_selected, resolved) != current_signature:
            next_change = candidate
            if candidate_selected is not None:
                next_component = candidate_selected[1]
                next_rate = _rate(next_component, resolved)
                next_period = next_component.period
            break
        candidate += _MINUTE

    return RateInfo(
        rate=_rate(component, resolved),
        quantity_unit=component.quantity_unit,
        period=component.period,
        period_label=component.period_label,
        season=current_season,
        component_index=component_index,
        next_change=next_change,
        next_rate=next_rate,
        next_period=next_period,
        is_dynamic=component.blocks is None and isinstance(component.rate, RateSource),
        blocked=component.blocks is not None,
    )


def _series_value(
    series: Sequence[tuple[datetime, datetime, Decimal | Mapping[str, Decimal]]],
    when: datetime,
) -> tuple[Decimal | Mapping[str, Decimal] | None, datetime | None]:
    value: Decimal | Mapping[str, Decimal] | None = None
    next_boundary: datetime | None = None
    for start, end, item in series:
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        if start_utc <= when < end_utc:
            value = item
        for boundary in (start_utc, end_utc):
            if boundary > when and (next_boundary is None or boundary < next_boundary):
                next_boundary = boundary
    return value, next_boundary


def _resolved_for(
    plan: PlanVersion,
    value: Decimal | Mapping[str, Decimal] | None,
) -> Mapping[str, Decimal] | None:
    if isinstance(value, Mapping):
        return value
    if value is not None:
        return {
            component.rate.entity: value
            for component in plan.components
            if isinstance(component, UsageComponent) and isinstance(component.rate, RateSource)
        }
    return None


def forecast(
    plan: PlanVersion,
    start: datetime,
    end: datetime,
    *,
    register: Register | str = Register.GENERAL,
    direction: Direction | str = Direction.IMPORT,
    resolved_series: Sequence[tuple[datetime, datetime, Decimal | Mapping[str, Decimal]]]
    | None = None,
) -> list[RateSlot]:
    """Split a requested interval at tariff and supplied dynamic-rate boundaries."""
    if (
        start.tzinfo is None
        or start.utcoffset() is None
        or end.tzinfo is None
        or end.utcoffset() is None
    ):
        raise ValueError("start and end must be timezone-aware")
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if end_utc <= start_utc:
        raise ValueError("end must be after start")

    series = resolved_series or ()
    for series_start, series_end, _ in series:
        if (
            series_start.tzinfo is None
            or series_start.utcoffset() is None
            or series_end.tzinfo is None
            or series_end.utcoffset() is None
        ):
            raise ValueError("resolved rate series boundaries must be timezone-aware")
        if series_end <= series_start:
            raise ValueError("resolved rate series end must be after its start")
    slots: list[RateSlot] = []
    cursor = start_utc
    while cursor < end_utc:
        value, series_boundary = _series_value(series, cursor)
        resolved = _resolved_for(plan, value)
        info = rate_at(plan, cursor, register=register, direction=direction, resolved=resolved)
        boundary = min(end_utc, cursor + _LOOKAHEAD)
        if info.next_change is not None and info.next_change < boundary:
            boundary = info.next_change
        if series_boundary is not None and series_boundary < boundary:
            boundary = series_boundary
        if boundary <= cursor:
            raise RuntimeError("rate forecast did not advance")
        slot = RateSlot(
            start=cursor.astimezone(start.tzinfo),
            end=boundary.astimezone(start.tzinfo),
            rate=info.rate,
            period=info.period,
        )
        if slots and slots[-1].rate == slot.rate and slots[-1].period == slot.period:
            previous = slots[-1]
            slots[-1] = RateSlot(previous.start, slot.end, previous.rate, previous.period)
        else:
            slots.append(slot)
        cursor = boundary
    return slots
