"""Turn interval usage into exact, unrounded tariff line items."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from zoneinfo import ZoneInfo

from tariff_core.contract import Contract
from tariff_core.engine.blocks import allocate_blocks, block_period_key
from tariff_core.engine.demand import demand_amount, demand_quantity
from tariff_core.models import (
    Commodity,
    DemandComponent,
    DemandReset,
    Direction,
    DiscountComponent,
    FixedComponent,
    FixedUnit,
    HolidayTreatment,
    PlanVersion,
    RateSource,
    Register,
    UsageComponent,
)
from tariff_core.schedule import holiday_dates, matches_schedule, season_at, to_local
from tariff_core.units import to_billed_quantity

RateResolver = Callable[[str, datetime], Decimal | None]


@dataclass(frozen=True, slots=True)
class Interval:
    end: datetime
    duration: timedelta
    register: Register | str
    unit: str
    import_qty: Decimal
    export_qty: Decimal = Decimal(0)
    demand_kw: Decimal | None = None
    demand_kva: Decimal | None = None

    def __post_init__(self) -> None:
        if self.end.tzinfo is None or self.end.utcoffset() is None:
            raise ValueError("interval end must be timezone-aware")
        if self.duration <= timedelta(0):
            raise ValueError("interval duration must be positive")
        if any(
            not isinstance(quantity, Decimal) or not quantity.is_finite()
            for quantity in (self.import_qty, self.export_qty)
        ):
            raise TypeError("interval quantities must be finite Decimal values")


@dataclass(frozen=True, slots=True)
class BillingPeriod:
    start: datetime
    end: datetime
    contract: Contract | None = None

    def __post_init__(self) -> None:
        if (
            self.start.tzinfo is None
            or self.start.utcoffset() is None
            or self.end.tzinfo is None
            or self.end.utcoffset() is None
        ):
            raise ValueError("billing period boundaries must be timezone-aware")
        if self.end.astimezone(UTC) <= self.start.astimezone(UTC):
            raise ValueError("billing period end must be after start")


@dataclass(frozen=True, slots=True)
class BillLineItem:
    component_index: int
    label: str
    register: Register | str | None
    direction: Direction | None
    period: str | None
    quantity: Decimal
    quantity_unit: str
    rate: Decimal
    amount: Decimal
    type: Literal["cost", "credit"]


LineItem = BillLineItem


@dataclass(frozen=True, slots=True)
class Bill:
    line_items: tuple[BillLineItem, ...]
    subtotal_cost: Decimal
    subtotal_credit: Decimal
    total: Decimal
    total_rounded: Decimal
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class _Amount:
    component_index: int
    label: str
    register: Register | str | None
    direction: Direction | None
    period: str | None
    quantity_unit: str
    quantity: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    rate_quantity: Decimal = Decimal(0)
    fixed_rate: Decimal | None = None
    credit: bool = False

    def add(self, quantity: Decimal, rate: Decimal, *, credit: bool = False) -> None:
        self.quantity += quantity
        self.rate_quantity += quantity * rate
        self.amount += abs(quantity * rate)
        self.credit = self.credit or credit

    def line_item(self) -> BillLineItem:
        rate = self.fixed_rate
        if rate is None:
            rate = self.rate_quantity / self.quantity if self.quantity else Decimal(0)
        return BillLineItem(
            component_index=self.component_index,
            label=self.label,
            register=self.register,
            direction=self.direction,
            period=self.period,
            quantity=self.quantity,
            quantity_unit=self.quantity_unit,
            rate=rate,
            amount=self.amount,
            type="credit" if self.credit else "cost",
        )


def _nominal_fixed_rate(component: FixedComponent) -> Decimal:
    if component.unit is FixedUnit.PER_DAY:
        return component.rate
    multiplier = {
        FixedUnit.PER_MONTH: Decimal(12),
        FixedUnit.PER_QUARTER: Decimal(4),
        FixedUnit.PER_YEAR: Decimal(1),
    }[component.unit]
    return component.rate * multiplier / Decimal(365)


def _duration_seconds(duration: timedelta) -> Decimal:
    return (
        Decimal(duration.days * 86400 + duration.seconds)
        + Decimal(duration.microseconds) / Decimal(1_000_000)
    )


def _local_midnight(day: date, timezone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone)


def _local_day_fraction(start: datetime, end: datetime, timezone: ZoneInfo) -> Decimal:
    """Measure a period as local calendar days, prorating partial days by elapsed time."""
    cursor = start.astimezone(timezone)
    stop = end.astimezone(timezone)
    amount = Decimal(0)
    while cursor.astimezone(UTC) < stop.astimezone(UTC):
        next_midnight = _local_midnight(cursor.date() + timedelta(days=1), timezone)
        boundary = min(next_midnight.astimezone(UTC), stop.astimezone(UTC))
        day_start = _local_midnight(cursor.date(), timezone).astimezone(UTC)
        day_end = next_midnight.astimezone(UTC)
        amount += _duration_seconds(boundary - cursor.astimezone(UTC)) / _duration_seconds(
            day_end - day_start
        )
        cursor = boundary.astimezone(timezone)
    return amount


def _breakpoints(
    start: datetime,
    end: datetime,
    plan: PlanVersion,
    contract: Contract | None = None,
) -> list[datetime]:
    """Split intervals at local day, season, and schedule boundaries."""
    timezone = ZoneInfo(plan.timezone or "UTC")
    start_local, end_local = start.astimezone(timezone), end.astimezone(timezone)
    boundaries = {start.astimezone(UTC), end.astimezone(UTC)}
    plans = [plan]
    if contract is not None:
        plans.extend(segment.plan for segment in contract.segments)
    schedules = []
    for candidate_plan in plans:
        schedules.extend(candidate_plan.schedules.values())
        schedules.extend(
            schedule
            for component in candidate_plan.components
            if (schedule := getattr(component, "schedule", None)) is not None
        )
    current = start_local.date()
    last = end_local.date()
    while current <= last:
        midnight = _local_midnight(current, timezone).astimezone(UTC)
        if start.astimezone(UTC) < midnight < end.astimezone(UTC):
            boundaries.add(midnight)
        for schedule in schedules:
            for window in schedule:
                for wall_time in window.time:
                    local_boundary = datetime.combine(current, wall_time, tzinfo=timezone)
                    utc_boundary = local_boundary.astimezone(UTC)
                    if start.astimezone(UTC) < utc_boundary < end.astimezone(UTC):
                        boundaries.add(utc_boundary)
        current += timedelta(days=1)
    if contract is not None:
        for segment in contract.segments:
            for boundary in (segment.from_, segment.to):
                if boundary is None:
                    continue
                local_boundary = (
                    boundary.astimezone(timezone)
                    if isinstance(boundary, datetime)
                    else _local_midnight(boundary, timezone)
                )
                utc_boundary = local_boundary.astimezone(UTC)
                if start.astimezone(UTC) < utc_boundary < end.astimezone(UTC):
                    boundaries.add(utc_boundary)
    return sorted(boundaries)


def _season_applies(component: object, plan: PlanVersion, when: datetime) -> bool:
    season = getattr(component, "season", None)
    return season is None or season_at(plan, when) == season


def _label(
    component: UsageComponent,
    plan: PlanVersion,
    tier_index: int | None = None,
) -> str:
    if component.label is not None:
        base = component.label.replace("_", " ").title()
    elif component.register in {Register.CONTROLLED_LOAD_1, Register.CONTROLLED_LOAD_2}:
        base = component.register.value.replace("_", " ").capitalize()
    elif component.period_label:
        base = component.period_label
    elif plan.commodity is Commodity.WATER:
        base = "Water usage"
    elif component.blocks is not None:
        base = "Usage"
    else:
        base = component.period.value.replace("_", " ").title() if component.period else "Usage"
    if tier_index is not None and component.blocks is not None:
        return f"{base} tier {tier_index + 1}"
    if component.period_label and component.label is None and component.register is Register.GENERAL:
        return f"{base} {component.direction.value}"
    return base


def _resolved_rate(
    component: UsageComponent,
    when: datetime,
    resolver: RateResolver | None,
    resolved_rates: Mapping[str, Decimal] | None,
) -> Decimal | None:
    if isinstance(component.rate, RateSource):
        raw = (
            resolver(component.rate.entity, when)
            if resolver is not None
            else (resolved_rates or {}).get(component.rate.entity)
        )
        if raw is None:
            return None
        if not isinstance(raw, Decimal) or not raw.is_finite():
            raise TypeError("dynamic rate resolver must return a finite Decimal or None")
        return (raw * component.rate.multiplier + component.rate.adder) * component.rate.unit_scale
    return component.rate


def _discount_matches(discount: DiscountComponent, component: object) -> bool:
    if "all" in discount.applies_to:
        return True
    if isinstance(component, UsageComponent):
        selectors = {
            "usage",
            f"usage.{component.direction.value}",
            f"usage.{component.register.value}",
        }
        return bool(selectors.intersection(discount.applies_to))
    if isinstance(component, FixedComponent):
        return "fixed" in discount.applies_to or f"fixed.{component.label.value}" in discount.applies_to
    if isinstance(component, DemandComponent):
        return "demand" in discount.applies_to
    return False


def bill(
    plan: PlanVersion,
    intervals: Iterable[Interval],
    *,
    period: BillingPeriod,
    resolved_rates: RateResolver | Mapping[str, Decimal] | None = None,
    include_conditional: bool = False,
) -> Bill:
    """Price interval-ending usage, splitting where local tariff rules change."""
    resolver = resolved_rates if callable(resolved_rates) else None
    resolved_map = resolved_rates if isinstance(resolved_rates, Mapping) else None
    normalized_intervals = tuple(intervals)
    if any(not isinstance(interval, Interval) for interval in normalized_intervals):
        raise TypeError("intervals must contain Interval values")
    contract = period.contract
    zone = ZoneInfo(plan.timezone or "UTC")
    period_days = _local_day_fraction(period.start, period.end, zone)
    amounts: dict[tuple[int, int | None], _Amount] = {}
    warnings: list[str] = []
    missing_intervals: dict[int, set[int]] = {}
    block_used: dict[tuple[int, tuple[int, ...]], Decimal] = {}

    def add_usage(
        component_index: int,
        component: UsageComponent,
        interval: Interval,
        quantity: Decimal,
        start: datetime,
        end: datetime,
        selected_plan: PlanVersion,
        interval_number: int,
    ) -> None:
        local = to_local(selected_plan, start)
        if not _season_applies(component, selected_plan, local):
            return
        if component.blocks is None:
            rate = _resolved_rate(component, start, resolver, resolved_map)
            if rate is None:
                missing_intervals.setdefault(component_index, set()).add(interval_number)
                return
            key = (component_index, None)
            amount = amounts.setdefault(
                key,
                _Amount(
                    component_index,
                    _label(component, selected_plan),
                    component.register,
                    component.direction,
                    component.period.value if component.period else None,
                    component.quantity_unit,
                ),
            )
            amount.add(quantity, rate, credit=component.direction is Direction.EXPORT or rate < 0)
            return

        blocks = component.blocks
        period_key = block_period_key(blocks.period.value, local.date(), period.start.astimezone(zone).date())
        used_key = (component_index, period_key)
        used = block_used.get(used_key, Decimal(0))
        split = allocate_blocks(blocks, quantity, used=used, period_days=period_days)
        block_used[used_key] = used + quantity
        for tier in split:
            key = (component_index, tier.tier_index)
            amount = amounts.setdefault(
                key,
                _Amount(
                    component_index,
                    _label(component, selected_plan, tier.tier_index),
                    component.register,
                    component.direction,
                    component.period.value if component.period else None,
                    component.quantity_unit,
                ),
            )
            amount.add(
                tier.quantity,
                tier.rate,
                credit=component.direction is Direction.EXPORT or tier.rate < 0,
            )

    for interval_number, interval in enumerate(normalized_intervals):
        interval_start = interval.end - interval.duration
        clip_start = max(interval_start.astimezone(UTC), period.start.astimezone(UTC))
        clip_end = min(interval.end.astimezone(UTC), period.end.astimezone(UTC))
        if clip_end <= clip_start:
            continue
        if contract is not None:
            try:
                interval_plan = contract.active_segment(
                    ((clip_start + (clip_end - clip_start) / 2).astimezone(interval.end.tzinfo))
                ).plan
            except ValueError:
                interval_plan = plan
        else:
            interval_plan = plan
        points = _breakpoints(clip_start, clip_end, interval_plan, contract)
        if len(points) > 2 and interval.duration > timedelta(hours=1):
            warnings.append(
                f"Interval ending {interval.end.isoformat()} split at tariff or block boundaries"
            )
        for slice_start, slice_end in zip(points, points[1:], strict=False):
            start = slice_start.astimezone(interval.end.tzinfo)
            end = slice_end.astimezone(interval.end.tzinfo)
            fraction = _duration_seconds(slice_end - slice_start) / _duration_seconds(
                interval.duration
            )
            slice_plan = interval_plan
            if contract is not None:
                try:
                    slice_plan = contract.active_segment(
                        ((slice_start + (slice_end - slice_start) / 2).astimezone(interval.end.tzinfo))
                    ).plan
                except ValueError:
                    pass
            for direction, raw_quantity in (
                (Direction.IMPORT, interval.import_qty),
                (Direction.EXPORT, interval.export_qty),
            ):
                meter_quantity = raw_quantity * fraction
                candidate_components = [
                    (index, component)
                    for index, component in enumerate(slice_plan.components)
                    if isinstance(component, UsageComponent)
                    and component.direction is direction
                    and (
                        component.quantity.from_register == interval.register
                        if component.quantity is not None
                        else component.register == interval.register
                    )
                ]
                if not candidate_components:
                    continue
                source_quantity = meter_quantity
                source_unit = interval.unit
                for index, component in candidate_components:
                    component_quantity = source_quantity
                    if component.quantity is not None:
                        component_quantity *= component.quantity.factor
                    try:
                        billed_quantity = to_billed_quantity(
                            component_quantity,
                            source_unit,
                            component.quantity_unit,
                            when=start,
                            register=interval.register,
                            conversions=contract.conversions if contract else (),
                        )
                    except ValueError as exc:
                        raise ValueError(
                            f"components[{index}] cannot convert {source_unit} to "
                            f"{component.quantity_unit}: {exc}"
                        ) from exc
                    local_start = to_local(slice_plan, start)
                    holidays = holiday_dates(slice_plan, local_start.date(), local_start.date())
                    if component.stack:
                        selected = (
                            (index, component)
                            if _season_applies(component, slice_plan, start)
                            and (
                                component.schedule is None
                                or matches_schedule(
                                    component.schedule,
                                    local_start,
                                    holiday_dates=holidays,
                                    holiday_treatment=(
                                        slice_plan.holidays.treatment
                                        if slice_plan.holidays is not None
                                        else HolidayTreatment.NONE
                                    ),
                                )
                            )
                            else None
                        )
                    else:
                        from tariff_core.engine.rates import select_component

                        selected = select_component(
                            slice_plan,
                            start,
                            register=component.register,
                            direction=component.direction,
                            holidays=holidays,
                        )
                    if selected is None or (not component.stack and selected[0] != index):
                        continue
                    add_usage(
                        index,
                        component,
                        interval,
                        billed_quantity,
                        start,
                        end,
                        slice_plan,
                        interval_number,
                    )

    for component_index, component in enumerate(plan.components):
        if not isinstance(component, FixedComponent):
            continue
        timezone = ZoneInfo(plan.timezone or "UTC")
        current = period.start.astimezone(timezone)
        stop = period.end.astimezone(timezone)
        total_days = _local_day_fraction(period.start, period.end, timezone)
        if total_days == 0 or not _season_applies(component, plan, current):
            continue
        rate = _nominal_fixed_rate(component)
        quantity = total_days
        key = (component_index, None)
        amount = amounts.setdefault(
            key,
            _Amount(
                component_index,
                component.label.value.replace("_", " ").capitalize(),
                component.register,
                None,
                None,
                "day",
                fixed_rate=rate,
            ),
        )
        amount.quantity += quantity
        amount.amount += abs(quantity * rate)
        amount.rate_quantity += quantity * rate

    demand_intervals = [
        interval
        for interval in normalized_intervals
        if interval.end.astimezone(UTC) > period.start.astimezone(UTC)
        and (interval.end - interval.duration).astimezone(UTC) < period.end.astimezone(UTC)
    ]
    for component_index, component in enumerate(plan.components):
        if not isinstance(component, DemandComponent):
            continue
        grouped: dict[tuple[int, ...], list[Interval]] = {}
        for interval in demand_intervals:
            local = to_local(plan, interval.end - interval.duration)
            if component.reset is DemandReset.MONTHLY:
                key = (local.year, local.month)
            else:
                key = (period.start.astimezone(zone).date().toordinal(),)
            grouped.setdefault(key, []).append(interval)
        for reset_key, group in grouped.items():
            demand = demand_quantity(component, group, plan)
            charge = demand_amount(component, demand)
            key = (component_index, hash(reset_key))
            line = amounts.setdefault(
                key,
                _Amount(
                    component_index,
                    "Demand",
                    None,
                    None,
                    None,
                    component.measure.value,
                    fixed_rate=component.rate,
                ),
            )
            line.quantity += max(Decimal(0), demand - (component.threshold or Decimal(0)))
            line.amount += charge
            line.rate_quantity += line.quantity * component.rate

    for component_index, component in enumerate(plan.components):
        if not isinstance(component, DiscountComponent):
            continue
        if component.conditional is not None and not include_conditional:
            continue
        discount_base = sum(
            (
                line.amount
                for (base_index, _), line in amounts.items()
                if base_index != component_index
                and base_index < len(plan.components)
                and _discount_matches(component, plan.components[base_index])
                and not line.credit
            ),
            Decimal(0),
        )
        discount_amount = discount_base * component.percent / Decimal(100)
        if discount_amount:
            amounts[(component_index, None)] = _Amount(
                component_index=component_index,
                label="Discount",
                register=None,
                direction=None,
                period=None,
                quantity_unit="currency",
                quantity=Decimal(1),
                amount=discount_amount,
                rate_quantity=-discount_amount,
                fixed_rate=-discount_amount,
                credit=True,
            )

    for component_index, count in missing_intervals.items():
        warnings.append(
            f"Dynamic rate missing for {len(count)} interval(s) on component {component_index}"
        )
    lines = tuple(
        line.line_item()
        for _, line in sorted(
            amounts.items(),
            key=lambda item: (item[0][0], -1 if item[0][1] is None else item[0][1]),
        )
        if line.quantity != 0 or line.amount != 0
    )
    subtotal_cost = sum((line.amount for line in lines if line.type == "cost"), Decimal(0))
    subtotal_credit = sum((line.amount for line in lines if line.type == "credit"), Decimal(0))
    total = subtotal_cost - subtotal_credit
    return Bill(
        line_items=lines,
        subtotal_cost=subtotal_cost,
        subtotal_credit=subtotal_credit,
        total=total,
        total_rounded=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        warnings=tuple(dict.fromkeys(warnings)),
    )
