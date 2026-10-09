"""Australian CDR Energy plan transformations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from tariff_core.errors import ParseError
from tariff_core.models import (
    Billing,
    BillingFrequency,
    BlockPeriod,
    Blocks,
    BlockTier,
    Commodity,
    Confidence,
    CustomerType,
    DemandComponent,
    DemandMeasure,
    DemandMethod,
    DemandReset,
    DemandUnit,
    Direction,
    Effective,
    FixedComponent,
    FixedLabel,
    FixedUnit,
    Holidays,
    HolidayTreatment,
    IncentiveComponent,
    Period,
    PlanVersion,
    PricingModel,
    Region,
    Register,
    Schedule,
    Source,
    SourceType,
    Supplier,
    Tax,
    TimeBasis,
    UsageComponent,
    Window,
)

_DAYS = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}
_RATE_PERIODS = {
    "PEAK": Period.PEAK,
    "OFF_PEAK": Period.OFF_PEAK,
    "SHOULDER": Period.SHOULDER,
    "SHOULDER1": Period.SOLAR_SOAK,
    "SOLAR_SOAK": Period.SOLAR_SOAK,
    "SINGLE": Period.SINGLE,
}
_INCLUSIVE_END = re.compile(r"^(?:[01]\d|2[0-3]):59$")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ParseError(path, "expected an object")
    return value


def _list(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ParseError(path, "expected a list")
    return value


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool):
        raise ParseError(path, "expected a decimal value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ParseError(path, "expected a decimal value") from exc
    if not result.is_finite():
        raise ParseError(path, "decimal value must be finite")
    return result


def _schedule(values: Any, path: str) -> Schedule:
    windows: list[Window] = []
    for index, raw in enumerate(_list(values, path)):
        window_path = f"{path}[{index}]"
        item = _mapping(raw, window_path)
        day_names = _list(item.get("days"), f"{window_path}.days")
        try:
            days = frozenset(_DAYS[str(day).upper()] for day in day_names)
        except KeyError as exc:
            raise ParseError(f"{window_path}.days", f"unknown day {exc.args[0]!r}") from exc

        start = item.get("startTime")
        end = item.get("endTime")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ParseError(window_path, "startTime and endTime must be strings")
        try:
            start_time = time.fromisoformat(start)
            end_time = time.fromisoformat(end)
        except (TypeError, ValueError) as exc:
            raise ParseError(window_path, "invalid time-of-use window") from exc
        if isinstance(end, str) and _INCLUSIVE_END.fullmatch(end):
            end_time = (datetime.combine(date.min, end_time) + timedelta(minutes=1)).time()
        windows.append(Window(days=days, time=(start_time, end_time)))
    return tuple(windows)


def _schedule_or_none(value: Any, path: str) -> Schedule | None:
    return _schedule(value, path) if value else None


def _period(value: Any) -> Period:
    return _RATE_PERIODS.get(str(value).upper(), Period.SINGLE)


def _quantity_unit(rate: Mapping[str, Any], commodity: Commodity) -> str:
    unit = str(rate.get("measureUnit", "")).upper()
    if unit in {"KWH", "KVAH"}:
        return "kWh"
    if unit == "MJ":
        return "MJ"
    if unit in {"M3", "M^3"}:
        return "m3"
    return "MJ" if commodity is Commodity.GAS else "kWh"


def _usage(
    rates: Any,
    *,
    path: str,
    commodity: Commodity,
    direction: Direction = Direction.IMPORT,
    register: Register = Register.GENERAL,
    schedule: Schedule | None = None,
    period: Period | None = None,
    period_label: str | None = None,
) -> list[UsageComponent]:
    entries = _list(rates, path)
    if not entries:
        raise ParseError(path, "at least one rate is required")
    parsed = [_mapping(entry, f"{path}[{i}]") for i, entry in enumerate(entries)]
    quantity_unit = _quantity_unit(parsed[0], commodity)
    values: list[BlockTier] = []
    for index, rate in enumerate(parsed):
        price = _decimal(rate.get("unitPrice"), f"{path}[{index}].unitPrice")
        if direction is Direction.EXPORT:
            price = -abs(price)
        threshold = (
            _decimal(rate["volume"], f"{path}[{index}].volume") if "volume" in rate else None
        )
        values.append(BlockTier(rate=price, up_to=threshold))

    if len(values) > 1 or values[0].up_to is not None:
        block_period = BlockPeriod.DAY if commodity is Commodity.GAS else BlockPeriod.BILLING_PERIOD
        return [
            UsageComponent(
                direction=direction,
                register=register,
                quantity_unit=quantity_unit,
                schedule=schedule,
                period=period,
                period_label=period_label,
                blocks=Blocks(period=block_period, prorate=False, tiers=tuple(values)),
            )
        ]

    return [
        UsageComponent(
            direction=direction,
            register=register,
            quantity_unit=quantity_unit,
            schedule=schedule,
            period=period,
            period_label=period_label,
            rate=values[0].rate,
        )
    ]


def _time_of_use(
    rates: Any,
    *,
    path: str,
    commodity: Commodity,
    direction: Direction = Direction.IMPORT,
) -> list[UsageComponent]:
    components: list[UsageComponent] = []
    for index, raw in enumerate(_list(rates, path)):
        rate = _mapping(raw, f"{path}[{index}]")
        components.extend(
            _usage(
                rate.get("rates"),
                path=f"{path}[{index}].rates",
                commodity=commodity,
                direction=direction,
                schedule=_schedule_or_none(
                    rate.get("timeOfUse", rate.get("timeVariations")),
                    f"{path}[{index}].timeOfUse",
                ),
                period=_period(rate.get("type")),
                period_label=rate.get("displayName"),
            )
        )
    return components


def _effective(value: Any) -> date:
    if not isinstance(value, str):
        raise ParseError("$.data.effectiveFrom", "expected an ISO date or datetime")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ParseError("$.data.effectiveFrom", "invalid ISO date or datetime") from exc


def _raw_hash(raw: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ParseError("$", "CDR data must contain JSON-compatible values") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def from_cdr(
    raw: Mapping[str, Any],
    *,
    retrieved_at: datetime,
    url: str | None = None,
) -> list[PlanVersion]:
    """Convert one CDR plan-detail response into a normalized plan."""
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    root = _mapping(raw, "$")
    data = _mapping(root.get("data"), "$.data")
    plan_id = data.get("planId")
    if not isinstance(plan_id, str) or not plan_id:
        raise ParseError("$.data.planId", "expected a non-empty string")

    if data.get("fuelType") == "ELECTRICITY":
        commodity = Commodity.ELECTRICITY
        contract_key = "electricityContract"
    elif data.get("fuelType") == "GAS":
        commodity = Commodity.GAS
        contract_key = "gasContract"
    else:
        raise ParseError("$.data.fuelType", "only electricity and gas plans are supported")
    contract = _mapping(data.get(contract_key), f"$.data.{contract_key}")

    components: list[Any] = []
    notes = [
        eligibility["information"]
        for raw_eligibility in _list(contract.get("eligibility", []), "$.data.contract.eligibility")
        if (eligibility := _mapping(raw_eligibility, "$.data.contract.eligibility[]")).get(
            "information"
        )
    ]
    for raw_incentive in _list(contract.get("incentives", []), "$.data.contract.incentives"):
        incentive = _mapping(raw_incentive, "$.data.contract.incentives[]")
        components.append(
            IncentiveComponent(
                label=incentive.get("displayName"),
                description=incentive.get("description"),
            )
        )

    for index, raw_period in enumerate(
        _list(contract.get("tariffPeriod", []), "$.data.contract.tariffPeriod")
    ):
        tariff = _mapping(raw_period, f"$.data.contract.tariffPeriod[{index}]")
        if tariff.get("rateBlockUType") == "timeOfUseRates":
            components.extend(
                _time_of_use(
                    tariff.get("timeOfUseRates"),
                    path=f"$.data.contract.tariffPeriod[{index}].timeOfUseRates",
                    commodity=commodity,
                )
            )
        elif tariff.get("rateBlockUType") == "singleRate":
            components.extend(
                _usage(
                    tariff.get("singleRate", {}).get("rates"),
                    path=f"$.data.contract.tariffPeriod[{index}].singleRate.rates",
                    commodity=commodity,
                    period=Period.SINGLE,
                    period_label=tariff.get("singleRate", {}).get("displayName"),
                )
            )
        if tariff.get("dailySupplyCharge") is not None:
            components.append(
                FixedComponent(
                    label=FixedLabel.SUPPLY,
                    unit=FixedUnit.PER_DAY,
                    rate=_decimal(
                        tariff["dailySupplyCharge"],
                        f"$.data.contract.tariffPeriod[{index}].dailySupplyCharge",
                    ),
                )
            )
        for demand_index, raw_demand in enumerate(tariff.get("demandCharges", [])):
            demand = _mapping(
                raw_demand, f"$.data.contract.tariffPeriod[{index}].demandCharges[{demand_index}]"
            )
            is_kva = "kva" in str(demand.get("displayName", "")).lower()
            is_daily = str(demand.get("chargePeriod", "")).upper() == "DAY"
            components.append(
                DemandComponent(
                    method=DemandMethod.MAX_INTERVAL_AVG,
                    measure=DemandMeasure.KVA if is_kva else DemandMeasure.KW,
                    unit=DemandUnit.PER_KW_PER_DAY
                    if is_daily
                    else DemandUnit.PER_KVA_PER_MONTH
                    if is_kva
                    else DemandUnit.PER_KW_PER_MONTH,
                    rate=_decimal(
                        demand.get("amount"),
                        f"$.data.contract.tariffPeriod[{index}].demandCharges[{demand_index}].amount",
                    ),
                    threshold=_decimal(
                        demand["minDemand"], "$.data.contract.demandCharges.minDemand"
                    )
                    if demand.get("minDemand") not in (None, "0", "0.00")
                    else None,
                    reset=DemandReset.MONTHLY,
                )
            )

    for index, raw_load in enumerate(
        _list(contract.get("controlledLoad", []), "$.data.contract.controlledLoad")
    ):
        load = _mapping(raw_load, f"$.data.contract.controlledLoad[{index}]")
        label = str(load.get("displayName", "")).lower()
        register = Register.CONTROLLED_LOAD_2 if "2" in label else Register.CONTROLLED_LOAD_1
        single = _mapping(
            load.get("singleRate"), f"$.data.contract.controlledLoad[{index}].singleRate"
        )
        components.extend(
            _usage(
                single.get("rates"),
                path=f"$.data.contract.controlledLoad[{index}].singleRate.rates",
                commodity=commodity,
                register=register,
                period=Period.SINGLE,
                period_label=single.get("displayName"),
            )
        )
        supply = single.get("dailySupplyCharge")
        if supply is not None and _decimal(supply, "controlledLoad.dailySupplyCharge") != 0:
            components.append(
                FixedComponent(
                    label=FixedLabel.SUPPLY,
                    register=register,
                    unit=FixedUnit.PER_DAY,
                    rate=_decimal(supply, "controlledLoad.dailySupplyCharge"),
                )
            )

    for index, raw_tariff in enumerate(
        _list(contract.get("solarFeedInTariff", []), "$.data.contract.solarFeedInTariff")
    ):
        tariff = _mapping(raw_tariff, f"$.data.contract.solarFeedInTariff[{index}]")
        if tariff.get("tariffUType") == "singleTariff":
            components.extend(
                _usage(
                    tariff.get("singleTariff", {}).get("rates"),
                    path=f"$.data.contract.solarFeedInTariff[{index}].singleTariff.rates",
                    commodity=commodity,
                    direction=Direction.EXPORT,
                    period=Period.SINGLE,
                    period_label=tariff.get("displayName"),
                )
            )
        elif tariff.get("tariffUType") == "timeVaryingTariffs":
            components.extend(
                _time_of_use(
                    tariff.get("timeVaryingTariffs"),
                    path=f"$.data.contract.solarFeedInTariff[{index}].timeVaryingTariffs",
                    commodity=commodity,
                    direction=Direction.EXPORT,
                )
            )

    for field in ("brand", "brandName", "geography"):
        if field not in data:
            raise ParseError(f"$.data.{field}", "missing required field")
    geography = _mapping(data["geography"], "$.data.geography")
    distributors = _list(geography.get("distributors", []), "$.data.geography.distributors")
    network_name = str(distributors[0]) if distributors else None
    network = network_name.lower().replace(" ", "_") if network_name else None
    is_queensland = network is not None and any(
        name in network for name in ("ergon", "energex", "brisbane")
    )
    frequencies = [
        BillingFrequency(value)
        for value in _list(contract.get("billFrequency", []), "$.data.contract.billFrequency")
        if value in {item.value for item in BillingFrequency}
    ]
    self_url = url
    if self_url is None:
        links = root.get("links", {})
        if isinstance(links, Mapping) and isinstance(links.get("self"), str):
            self_url = links["self"]

    result = PlanVersion(
        commodity=commodity,
        plan_id=f"au:cdr:{plan_id}",
        display_name=data.get("displayName"),
        supplier=Supplier(id=str(data["brand"]), name=str(data["brandName"])),
        customer_type=CustomerType.BUSINESS
        if data.get("customerType") == "BUSINESS"
        else CustomerType.RESIDENTIAL,
        region=Region(country="AU", network=network),
        currency="AUD",
        tax=Tax(inclusive=True, rate=Decimal("0.10")),
        timezone="Australia/Brisbane" if is_queensland else "Australia/Sydney",
        time_basis=TimeBasis.LOCAL,
        holidays=Holidays(calendar="AU-QLD", treatment=HolidayTreatment.NONE)
        if is_queensland
        else Holidays(),
        effective=Effective(from_=_effective(data.get("effectiveFrom"))),
        pricing_model=PricingModel.BUNDLED,
        billing=Billing(frequencies=tuple(frequencies)),
        components=tuple(components),
        source=Source(
            type=SourceType.OFFICIAL_FEED,
            feed="au_cdr",
            url=self_url,
            retrieved_at=retrieved_at,
            raw_sha256=_raw_hash(root),
        ),
        confidence=Confidence.HIGH,
        notes=tuple(notes),
    )
    return [result]
