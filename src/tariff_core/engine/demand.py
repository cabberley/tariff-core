"""Demand charge evaluation from interval power measurements."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from tariff_core.models import DemandComponent, DemandMethod
from tariff_core.schedule import matches_schedule, season_at, to_local


def demand_quantity(component: DemandComponent, intervals: Iterable[object], plan: object) -> Decimal:
    """Return the chargeable demand over matching interval measurements."""
    values: list[Decimal] = []
    for interval in intervals:
        end = interval.end  # type: ignore[attr-defined]
        start = end - interval.duration  # type: ignore[attr-defined]
        local_start = to_local(plan, start)  # type: ignore[arg-type]
        if component.season is not None and season_at(plan, local_start) != component.season:  # type: ignore[arg-type]
            continue
        if component.schedule is not None and not matches_schedule(component.schedule, local_start):
            continue
        quantity = interval.import_qty  # type: ignore[attr-defined]
        if component.method is DemandMethod.MAX_INSTANT:
            value = interval.demand_kva if component.measure.value == "kVA" else interval.demand_kw  # type: ignore[attr-defined]
            if value is None:
                continue
        elif component.method is DemandMethod.MAX_INTERVAL_AVG:
            value = quantity / _duration_hours(interval.duration)  # type: ignore[attr-defined]
        else:
            value = quantity / _duration_hours(interval.duration)  # type: ignore[attr-defined]
        values.append(value)
    if not values:
        return Decimal(0)
    if component.method is DemandMethod.TOP_N_AVG and component.top_n:
        selected = sorted(values, reverse=True)[: component.top_n]
        return sum(selected, Decimal(0)) / Decimal(len(selected))
    return max(values)


def _duration_hours(duration: object) -> Decimal:
    seconds = duration.days * 86400 + duration.seconds  # type: ignore[attr-defined]
    return Decimal(seconds) / Decimal(3600)


def demand_amount(component: DemandComponent, demand: Decimal) -> Decimal:
    """Apply the demand threshold and convert the monthly/day rate to a charge."""
    chargeable = max(Decimal(0), demand - (component.threshold or Decimal(0)))
    return chargeable * component.rate
