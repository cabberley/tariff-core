"""Strict parsing of plan data into tariff models."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence, Set
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from tariff_core.contract import Contract, ContractSegment, Conversion
from tariff_core.errors import ParseError
from tariff_core.models import (
    Billing,
    BillingFrequency,
    BlockPeriod,
    Blocks,
    BlockTier,
    Commodity,
    Component,
    Confidence,
    CustomerType,
    DemandComponent,
    DemandMeasure,
    DemandMethod,
    DemandReset,
    DemandUnit,
    DerivedQuantity,
    Direction,
    DiscountComponent,
    Effective,
    FixedComponent,
    FixedLabel,
    FixedUnit,
    Holidays,
    HolidayTreatment,
    IncentiveComponent,
    MonthDay,
    Period,
    PlanKind,
    PlanVersion,
    PricingModel,
    RateSource,
    Region,
    Register,
    Schedule,
    Season,
    Source,
    SourceType,
    Supplier,
    Tax,
    TimeBasis,
    UsageComponent,
    Window,
)

_DAY_NUMBERS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_MONTH_DAY_PATTERN = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _object(
    value: Any, path: str, allowed: Set[str], required: Set[str] = frozenset()
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ParseError(path, "expected an object")
    for key in value:
        if not isinstance(key, str):
            raise ParseError(path, "object keys must be strings")
        if key not in allowed:
            raise ParseError(f"{path}.{key}", "unknown key")
    for key in required:
        if key not in value:
            raise ParseError(f"{path}.{key}", "missing required field")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ParseError(path, "expected a list")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ParseError(path, "expected a string")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ParseError(path, "expected a boolean")
    return value


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(path, "expected an integer")
    return value


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, float):
        raise ParseError(path, "floating-point values are not allowed; use a decimal string")
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ParseError(path, "expected a decimal string or integer")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ParseError(path, "invalid decimal value") from exc
    if not parsed.is_finite():
        raise ParseError(path, "decimal value must be finite")
    return parsed


def _enum[E: StrEnum](value: Any, enum_type: type[E], path: str) -> E:
    if not isinstance(value, str):
        raise ParseError(path, "expected a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        choices = ", ".join(member.value for member in enum_type)
        raise ParseError(path, f"expected one of: {choices}") from exc


def _date(value: Any, path: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, (str, date)):
        raise ParseError(path, "expected a date in YYYY-MM-DD format")
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(path, "expected a date in YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise ParseError(path, "expected a date in YYYY-MM-DD format")
    return parsed


def _datetime(value: Any, path: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ParseError(path, "expected an ISO 8601 datetime") from exc
    else:
        raise ParseError(path, "expected an ISO 8601 datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ParseError(path, "datetime must include a timezone")
    return parsed


def _parse_month_day(value: Any, path: str) -> MonthDay:
    text = _string(value, path)
    if not _MONTH_DAY_PATTERN.fullmatch(text):
        raise ParseError(path, "expected a month-day in MM-DD format")
    month, day = (int(part) for part in text.split("-"))
    try:
        return MonthDay(month, day)
    except ValueError as exc:
        raise ParseError(path, "invalid month-day") from exc


def _parse_season(value: Any, path: str) -> Season:
    obj = _object(value, path, {"from", "to", "months"})
    if "months" in obj:
        if "from" in obj or "to" in obj:
            raise ParseError(path, "use either months or from/to")
        months_raw = _sequence(obj["months"], f"{path}.months")
        months: list[int] = []
        for index, item in enumerate(months_raw):
            month = _integer(item, f"{path}.months[{index}]")
            if month < 1 or month > 12:
                raise ParseError(f"{path}.months[{index}]", "month must be between 1 and 12")
            if month in months:
                raise ParseError(f"{path}.months[{index}]", "months must not be repeated")
            months.append(month)
        if not months:
            raise ParseError(f"{path}.months", "at least one month is required")
        members = set(months)
        if len(members) == 12:
            return Season(MonthDay(1, 1), MonthDay(1, 1))
        starts = [month for month in months if (month - 2) % 12 + 1 not in members]
        if len(starts) != 1:
            raise ParseError(f"{path}.months", "months must be contiguous")
        start = starts[0]
        end_month = start
        while end_month in members:
            end_month = end_month % 12 + 1
        return Season(MonthDay(start, 1), MonthDay(end_month, 1))
    if "from" not in obj or "to" not in obj:
        raise ParseError(path, "expected from and to")
    return Season(
        _parse_month_day(obj["from"], f"{path}.from"),
        _parse_month_day(obj["to"], f"{path}.to"),
    )


def _parse_time(value: Any, path: str, *, end: bool = False) -> time:
    text = _string(value, path)
    if text == "24:00" and end:
        return time.min
    if not _TIME_PATTERN.fullmatch(text):
        raise ParseError(path, "expected a time in HH:MM format")
    return time.fromisoformat(text)


def _parse_days(value: Any, path: str) -> frozenset[int]:
    if isinstance(value, str):
        normalized = value.lower()
        if normalized == "all":
            return frozenset(range(7))
        if normalized == "weekdays":
            return frozenset(range(5))
        if normalized == "weekends":
            return frozenset({5, 6})
        values: Sequence[Any] = (value,)
    else:
        values = _sequence(value, path)
    days: set[int] = set()
    for index, item in enumerate(values):
        day_path = f"{path}[{index}]" if not isinstance(value, str) else path
        if not isinstance(item, str):
            raise ParseError(day_path, "expected a day name")
        try:
            days.add(_DAY_NUMBERS[item.lower()])
        except KeyError as exc:
            raise ParseError(day_path, "expected a day name from mon to sun") from exc
    return frozenset(days)


def _parse_window(value: Any, path: str) -> Window:
    obj = _object(value, path, {"days", "time"}, {"time"})
    times = _sequence(obj["time"], f"{path}.time")
    if len(times) != 2:
        raise ParseError(f"{path}.time", "expected exactly two times")
    return Window(
        days=_parse_days(obj.get("days", "all"), f"{path}.days"),
        time=(
            _parse_time(times[0], f"{path}.time[0]"),
            _parse_time(times[1], f"{path}.time[1]", end=True),
        ),
    )


def _parse_schedule(value: Any, path: str, schedules: Mapping[str, Schedule]) -> Schedule:
    if isinstance(value, str):
        try:
            return schedules[value]
        except KeyError as exc:
            raise ParseError(path, f"unknown schedule name {value!r}") from exc
    if isinstance(value, Mapping):
        return (_parse_window(value, path),)
    windows = _sequence(value, path)
    return tuple(_parse_window(window, f"{path}[{index}]") for index, window in enumerate(windows))


def _parse_blocks(value: Any, path: str) -> Blocks:
    obj = _object(value, path, {"period", "prorate", "tiers"}, {"period", "tiers"})
    tiers_raw = _sequence(obj["tiers"], f"{path}.tiers")
    tiers: list[BlockTier] = []
    for index, raw_tier in enumerate(tiers_raw):
        tier_path = f"{path}.tiers[{index}]"
        tier = _object(raw_tier, tier_path, {"up_to", "rate"}, {"rate"})
        tiers.append(
            BlockTier(
                rate=_decimal(tier["rate"], f"{tier_path}.rate"),
                up_to=(_decimal(tier["up_to"], f"{tier_path}.up_to") if "up_to" in tier else None),
            )
        )
    return Blocks(
        period=_enum(obj["period"], BlockPeriod, f"{path}.period"),
        prorate=_boolean(obj.get("prorate", False), f"{path}.prorate"),
        tiers=tuple(tiers),
    )


def _component_schedule(
    obj: Mapping[str, Any], path: str, schedules: Mapping[str, Schedule]
) -> Schedule | None:
    if "schedule" not in obj:
        return None
    return _parse_schedule(obj["schedule"], f"{path}.schedule", schedules)


def _parse_component(value: Any, path: str, schedules: Mapping[str, Schedule]) -> Component:
    if not isinstance(value, Mapping):
        raise ParseError(path, "expected an object")
    if "kind" not in value:
        raise ParseError(f"{path}.kind", "missing required field")
    kind = _string(value["kind"], f"{path}.kind")
    if kind == "fixed":
        allowed = {"kind", "label", "register", "unit", "rate", "season"}
        obj = _object(value, path, allowed, {"label", "unit", "rate"})
        return FixedComponent(
            label=_enum(obj["label"], FixedLabel, f"{path}.label"),
            register=_enum(obj["register"], Register, f"{path}.register")
            if "register" in obj
            else None,
            unit=_enum(obj["unit"], FixedUnit, f"{path}.unit"),
            rate=_decimal(obj["rate"], f"{path}.rate"),
            season=_string(obj["season"], f"{path}.season") if "season" in obj else None,
        )
    if kind == "usage":
        allowed = {
            "kind",
            "direction",
            "register",
            "quantity_unit",
            "schedule",
            "season",
            "period",
            "period_label",
            "rate",
            "blocks",
            "quantity",
            "label",
            "stack",
        }
        obj = _object(value, path, allowed)
        quantity = None
        if "quantity" in obj:
            quantity_obj = _object(
                obj["quantity"], f"{path}.quantity", {"from", "factor"}, {"from", "factor"}
            )
            quantity = DerivedQuantity(
                from_register=_enum(quantity_obj["from"], Register, f"{path}.quantity.from"),
                factor=_decimal(quantity_obj["factor"], f"{path}.quantity.factor"),
            )
        rate: Decimal | RateSource | None = None
        if "rate" in obj:
            raw_rate = obj["rate"]
            if isinstance(raw_rate, Mapping):
                rate_obj = _object(
                    raw_rate,
                    f"{path}.rate",
                    {"entity", "multiplier", "adder", "unit_scale"},
                    {"entity"},
                )
                rate = RateSource(
                    entity=_string(rate_obj["entity"], f"{path}.rate.entity"),
                    multiplier=_decimal(rate_obj.get("multiplier", "1"), f"{path}.rate.multiplier"),
                    adder=_decimal(rate_obj.get("adder", "0"), f"{path}.rate.adder"),
                    unit_scale=_decimal(rate_obj.get("unit_scale", "1"), f"{path}.rate.unit_scale"),
                )
            else:
                rate = _decimal(raw_rate, f"{path}.rate")
        return UsageComponent(
            direction=_enum(obj.get("direction", "import"), Direction, f"{path}.direction"),
            register=_enum(obj.get("register", "general"), Register, f"{path}.register"),
            quantity_unit=_string(obj.get("quantity_unit", "kWh"), f"{path}.quantity_unit"),
            schedule=_component_schedule(obj, path, schedules),
            season=_string(obj["season"], f"{path}.season") if "season" in obj else None,
            period=_enum(obj["period"], Period, f"{path}.period") if "period" in obj else None,
            period_label=_string(obj["period_label"], f"{path}.period_label")
            if "period_label" in obj
            else None,
            rate=rate,
            blocks=_parse_blocks(obj["blocks"], f"{path}.blocks") if "blocks" in obj else None,
            quantity=quantity,
            label=_string(obj["label"], f"{path}.label") if "label" in obj else None,
            stack=_boolean(obj.get("stack", False), f"{path}.stack"),
        )
    if kind == "demand":
        allowed = {
            "kind",
            "schedule",
            "method",
            "interval_minutes",
            "top_n",
            "measure",
            "threshold",
            "reset",
            "unit",
            "rate",
            "season",
        }
        obj = _object(value, path, allowed, {"method", "measure", "unit", "rate"})
        return DemandComponent(
            method=_enum(obj["method"], DemandMethod, f"{path}.method"),
            measure=_enum(obj["measure"], DemandMeasure, f"{path}.measure"),
            unit=_enum(obj["unit"], DemandUnit, f"{path}.unit"),
            rate=_decimal(obj["rate"], f"{path}.rate"),
            schedule=_component_schedule(obj, path, schedules),
            season=_string(obj["season"], f"{path}.season") if "season" in obj else None,
            interval_minutes=(
                _integer(obj["interval_minutes"], f"{path}.interval_minutes")
                if "interval_minutes" in obj
                else None
            ),
            top_n=_integer(obj["top_n"], f"{path}.top_n") if "top_n" in obj else None,
            threshold=_decimal(obj["threshold"], f"{path}.threshold")
            if "threshold" in obj
            else None,
            reset=_enum(obj.get("reset", "monthly"), DemandReset, f"{path}.reset"),
        )
    if kind == "discount":
        allowed = {"kind", "applies_to", "percent", "conditional", "schedule", "season"}
        obj = _object(value, path, allowed, {"applies_to", "percent"})
        applies_to_raw = _sequence(obj["applies_to"], f"{path}.applies_to")
        return DiscountComponent(
            applies_to=tuple(
                _string(item, f"{path}.applies_to[{index}]")
                for index, item in enumerate(applies_to_raw)
            ),
            percent=_decimal(obj["percent"], f"{path}.percent"),
            conditional=(
                _string(obj["conditional"], f"{path}.conditional")
                if obj.get("conditional") is not None
                else None
            ),
            schedule=_component_schedule(obj, path, schedules),
            season=_string(obj["season"], f"{path}.season") if "season" in obj else None,
        )
    if kind == "incentive":
        obj = _object(value, path, {"kind", "label", "description", "value", "details"})
        raw_value = obj.get("value")
        if isinstance(raw_value, (float, int, Decimal)):
            raw_value = _decimal(raw_value, f"{path}.value")
        elif raw_value is not None and not isinstance(raw_value, str):
            raise ParseError(f"{path}.value", "expected a string or decimal")
        details = obj.get("details", {})
        if not isinstance(details, Mapping):
            raise ParseError(f"{path}.details", "expected an object")
        return IncentiveComponent(
            label=_string(obj["label"], f"{path}.label") if "label" in obj else None,
            description=(
                _string(obj["description"], f"{path}.description") if "description" in obj else None
            ),
            value=raw_value,
            details=dict(details),
        )
    raise ParseError(f"{path}.kind", f"unknown component kind {kind!r}")


def _parse_source(value: Any, path: str) -> Source:
    known = {"type", "feed", "url", "retrieved_at", "raw_sha256"}
    if not isinstance(value, Mapping):
        raise ParseError(path, "expected an object")
    for key in value:
        if not isinstance(key, str):
            raise ParseError(path, "object keys must be strings")
    return Source(
        type=_enum(value["type"], SourceType, f"{path}.type") if "type" in value else None,
        feed=_string(value["feed"], f"{path}.feed") if "feed" in value else None,
        url=_string(value["url"], f"{path}.url") if "url" in value else None,
        retrieved_at=(
            _datetime(value["retrieved_at"], f"{path}.retrieved_at")
            if "retrieved_at" in value
            else None
        ),
        raw_sha256=(
            _string(value["raw_sha256"], f"{path}.raw_sha256") if "raw_sha256" in value else None
        ),
        extra={key: item for key, item in value.items() if key not in known},
    )


def _parse_plan(value: Any) -> PlanVersion:
    fields = {
        "id",
        "plan_id",
        "kind",
        "display_name",
        "supplier",
        "commodity",
        "bundle_id",
        "customer_type",
        "region",
        "currency",
        "tax",
        "timezone",
        "time_basis",
        "holidays",
        "effective",
        "pricing_model",
        "network_tariff_ref",
        "billing",
        "seasons",
        "schedules",
        "components",
        "source",
        "confidence",
        "partial",
        "unmapped_features",
        "notes",
    }
    obj = _object(value, "$", fields, {"commodity"})
    schedules_raw = obj.get("schedules", {})
    if not isinstance(schedules_raw, Mapping):
        raise ParseError("$.schedules", "expected an object")
    schedules: dict[str, Schedule] = {}
    for name, raw_schedule in schedules_raw.items():
        if not isinstance(name, str):
            raise ParseError("$.schedules", "schedule names must be strings")
        schedules[name] = _parse_schedule(raw_schedule, f"$.schedules.{name}", {})
    components_raw = _sequence(obj.get("components", ()), "$.components")
    components = tuple(
        _parse_component(item, f"$.components[{index}]", schedules)
        for index, item in enumerate(components_raw)
    )

    supplier = None
    if "supplier" in obj and obj["supplier"] is not None:
        supplier_obj = _object(obj["supplier"], "$.supplier", {"id", "name"}, {"id", "name"})
        supplier = Supplier(
            id=_string(supplier_obj["id"], "$.supplier.id"),
            name=_string(supplier_obj["name"], "$.supplier.name"),
        )

    region = None
    if "region" in obj and obj["region"] is not None:
        region_obj = _object(obj["region"], "$.region", {"country", "network", "zone"}, {"country"})
        region = Region(
            country=_string(region_obj["country"], "$.region.country"),
            network=_string(region_obj["network"], "$.region.network")
            if "network" in region_obj and region_obj["network"] is not None
            else None,
            zone=_string(region_obj["zone"], "$.region.zone")
            if "zone" in region_obj and region_obj["zone"] is not None
            else None,
        )

    tax = None
    if "tax" in obj and obj["tax"] is not None:
        tax_obj = _object(obj["tax"], "$.tax", {"inclusive", "rate"}, {"inclusive", "rate"})
        tax = Tax(
            inclusive=_boolean(tax_obj["inclusive"], "$.tax.inclusive"),
            rate=_decimal(tax_obj["rate"], "$.tax.rate"),
        )

    holidays = None
    if "holidays" in obj and obj["holidays"] is not None:
        holidays_obj = _object(obj["holidays"], "$.holidays", {"calendar", "treatment"})
        holidays = Holidays(
            calendar=_string(holidays_obj["calendar"], "$.holidays.calendar")
            if "calendar" in holidays_obj and holidays_obj["calendar"] is not None
            else None,
            treatment=_enum(
                holidays_obj.get("treatment", "none"), HolidayTreatment, "$.holidays.treatment"
            ),
        )

    effective = None
    if "effective" in obj and obj["effective"] is not None:
        effective_obj = _object(obj["effective"], "$.effective", {"from", "to"})
        effective = Effective(
            from_=_date(effective_obj["from"], "$.effective.from")
            if "from" in effective_obj and effective_obj["from"] is not None
            else None,
            to=_date(effective_obj["to"], "$.effective.to")
            if "to" in effective_obj and effective_obj["to"] is not None
            else None,
        )

    billing = Billing()
    if "billing" in obj and obj["billing"] is not None:
        billing_obj = _object(obj["billing"], "$.billing", {"frequencies"})
        frequencies = _sequence(billing_obj.get("frequencies", ()), "$.billing.frequencies")
        billing = Billing(
            tuple(
                _enum(item, BillingFrequency, f"$.billing.frequencies[{index}]")
                for index, item in enumerate(frequencies)
            )
        )

    seasons_raw = obj.get("seasons", {})
    if not isinstance(seasons_raw, Mapping):
        raise ParseError("$.seasons", "expected an object")
    seasons = {
        _string(name, "$.seasons"): _parse_season(season, f"$.seasons.{name}")
        for name, season in seasons_raw.items()
    }

    source = (
        _parse_source(obj["source"], "$.source")
        if "source" in obj and obj["source"] is not None
        else None
    )
    notes = _sequence(obj.get("notes", ()), "$.notes")
    unmapped = _sequence(obj.get("unmapped_features", ()), "$.unmapped_features")

    return PlanVersion(
        id=_string(obj["id"], "$.id") if "id" in obj and obj["id"] is not None else None,
        plan_id=_string(obj["plan_id"], "$.plan_id")
        if "plan_id" in obj and obj["plan_id"] is not None
        else None,
        kind=_enum(obj.get("kind", "retail"), PlanKind, "$.kind"),
        display_name=_string(obj["display_name"], "$.display_name")
        if "display_name" in obj and obj["display_name"] is not None
        else None,
        supplier=supplier,
        commodity=_enum(obj["commodity"], Commodity, "$.commodity"),
        bundle_id=_string(obj["bundle_id"], "$.bundle_id")
        if "bundle_id" in obj and obj["bundle_id"] is not None
        else None,
        customer_type=_enum(
            obj.get("customer_type", "residential"), CustomerType, "$.customer_type"
        ),
        region=region,
        currency=_string(obj["currency"], "$.currency")
        if "currency" in obj and obj["currency"] is not None
        else None,
        tax=tax,
        timezone=_string(obj["timezone"], "$.timezone")
        if "timezone" in obj and obj["timezone"] is not None
        else None,
        time_basis=_enum(obj.get("time_basis", "local"), TimeBasis, "$.time_basis"),
        holidays=holidays,
        effective=effective,
        pricing_model=_enum(obj.get("pricing_model", "bundled"), PricingModel, "$.pricing_model"),
        network_tariff_ref=(
            _string(obj["network_tariff_ref"], "$.network_tariff_ref")
            if "network_tariff_ref" in obj and obj["network_tariff_ref"] is not None
            else None
        ),
        billing=billing,
        seasons=seasons,
        schedules=schedules,
        components=components,
        source=source,
        confidence=(
            _enum(obj["confidence"], Confidence, "$.confidence")
            if "confidence" in obj and obj["confidence"] is not None
            else None
        ),
        partial=_boolean(obj.get("partial", False), "$.partial"),
        unmapped_features=tuple(unmapped),
        notes=tuple(notes),
    )


def parse_plan(data: Mapping[str, Any] | str) -> PlanVersion:
    """Parse a mapping, JSON document, or optional YAML document into a plan."""
    if isinstance(data, str):
        try:
            value = json.loads(
                data,
                parse_float=Decimal,
                parse_constant=lambda token: _invalid_json_constant(token),
            )
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as exc:
                raise ParseError(
                    "$", "input is not valid JSON; install tariff-core[yaml] to parse YAML"
                ) from exc
            try:
                value = yaml.safe_load(data)
            except yaml.YAMLError as exc:
                raise ParseError("$", f"invalid YAML: {exc}") from exc
        except ValueError as exc:
            raise ParseError("$", str(exc)) from exc
    elif isinstance(data, Mapping):
        value = data
    else:
        raise ParseError("$", "expected a mapping or JSON/YAML string")
    return _parse_plan(value)


def _contract_boundary(value: Any, path: str) -> date | datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ParseError(path, "datetime must include a timezone")
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            return _datetime(value, path)
        if parsed_date.isoformat() == value:
            return parsed_date
    raise ParseError(path, "expected an ISO date or timezone-aware datetime")


def parse_contract(data: Mapping[str, Any] | str) -> Contract:
    """Parse a mapping, JSON document, or optional YAML document into a contract."""
    if isinstance(data, str):
        try:
            value = json.loads(
                data,
                parse_float=Decimal,
                parse_constant=lambda token: _invalid_json_constant(token),
            )
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError as exc:
                raise ParseError(
                    "$", "input is not valid JSON; install tariff-core[yaml] to parse YAML"
                ) from exc
            try:
                value = yaml.safe_load(data)
            except yaml.YAMLError as exc:
                raise ParseError("$", f"invalid YAML: {exc}") from exc
        except ValueError as exc:
            raise ParseError("$", str(exc)) from exc
    elif isinstance(data, Mapping):
        value = data
    else:
        raise ParseError("$", "expected a mapping or JSON/YAML string")

    obj = _object(value, "$", {"commodity", "meters", "conversions", "segments"}, {"commodity"})
    commodity = _enum(obj["commodity"], Commodity, "$.commodity")

    raw_meters = obj.get("meters", {})
    if not isinstance(raw_meters, Mapping):
        raise ParseError("$.meters", "expected an object")
    meters: dict[str, str] = {}
    for register, unit in raw_meters.items():
        if not isinstance(register, str):
            raise ParseError("$.meters", "register names must be strings")
        meters[register] = _string(unit, f"$.meters.{register}")

    conversions: list[Conversion] = []
    for index, raw in enumerate(_sequence(obj.get("conversions", ()), "$.conversions")):
        path = f"$.conversions[{index}]"
        conversion = _object(
            raw,
            path,
            {
                "from",
                "register",
                "meter_unit",
                "billed_unit",
                "heating_value",
                "correction_factor",
            },
            {"from", "register", "meter_unit", "billed_unit", "heating_value", "correction_factor"},
        )
        conversions.append(
            Conversion(
                from_=_date(conversion["from"], f"{path}.from"),
                register=_enum(conversion["register"], Register, f"{path}.register"),
                meter_unit=_string(conversion["meter_unit"], f"{path}.meter_unit"),
                billed_unit=_string(conversion["billed_unit"], f"{path}.billed_unit"),
                heating_value=_decimal(conversion["heating_value"], f"{path}.heating_value"),
                correction_factor=_decimal(
                    conversion["correction_factor"], f"{path}.correction_factor"
                ),
            )
        )

    segments: list[ContractSegment] = []
    for index, raw in enumerate(_sequence(obj.get("segments", ()), "$.segments")):
        path = f"$.segments[{index}]"
        segment = _object(
            raw,
            path,
            {"from", "to", "plan", "overrides", "catalogue_ref"},
            {"from", "plan"},
        )
        plan = segment["plan"]
        if not isinstance(plan, Mapping):
            raise ParseError(f"{path}.plan", "expected an object")
        overrides = _sequence(segment.get("overrides", ()), f"{path}.overrides")
        if any(not isinstance(override, Mapping) for override in overrides):
            raise ParseError(f"{path}.overrides", "expected a list of objects")
        segments.append(
            ContractSegment(
                from_=_contract_boundary(segment["from"], f"{path}.from"),
                to=_contract_boundary(segment["to"], f"{path}.to")
                if segment.get("to") is not None
                else None,
                plan=_parse_plan(plan),
                overrides=tuple(overrides),
                catalogue_ref=_string(segment["catalogue_ref"], f"{path}.catalogue_ref")
                if segment.get("catalogue_ref") is not None
                else None,
            )
        )
    return Contract(commodity, meters, tuple(conversions), tuple(segments))


def _invalid_json_constant(token: str) -> None:
    raise ValueError(f"invalid JSON numeric constant {token}")
