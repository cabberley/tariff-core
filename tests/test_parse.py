"""Tests for strict tariff model parsing."""

import builtins
from dataclasses import FrozenInstanceError
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tariff_core import (
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
    ParseError,
    Period,
    PlanKind,
    PlanVersion,
    PricingModel,
    RateSource,
    Region,
    Register,
    Season,
    Source,
    SourceType,
    Supplier,
    Tax,
    TimeBasis,
    UsageComponent,
    Window,
    parse_plan,
)

FIXTURES = Path(__file__).parent / "fixtures" / "plans"


def test_origin_ergon_yaml_fixture_matches_models() -> None:
    expected = PlanVersion(
        id="au:cdr:ORI1161031MRE3@EME@2026-10-01#3fa1c2d9",
        plan_id="au:cdr:ORI1161031MRE3@EME",
        kind=PlanKind.RETAIL,
        display_name="Origin Go Variable Ongoing - Ergon",
        supplier=Supplier("origin", "Origin Energy"),
        commodity=Commodity.ELECTRICITY,
        customer_type=CustomerType.RESIDENTIAL,
        region=Region("AU", "ergon", None),
        currency="AUD",
        tax=Tax(True, Decimal("0.10")),
        timezone="Australia/Brisbane",
        time_basis=TimeBasis.LOCAL,
        holidays=Holidays("AU-QLD", HolidayTreatment.NONE),
        effective=Effective(date(2026, 10, 1), None),
        pricing_model=PricingModel.BUNDLED,
        billing=Billing((BillingFrequency.P1M, BillingFrequency.P3M)),
        schedules={
            "peak": (Window(frozenset(range(7)), (time(16), time(21))),),
            "shoulder": (Window(frozenset(range(7)), (time(7), time(16))),),
            "off_peak": (Window(frozenset(range(7)), (time(21), time(7))),),
            "controlled_load": (Window(frozenset(range(7)), (time(22), time(7))),),
        },
        components=(
            FixedComponent(FixedLabel.SUPPLY, FixedUnit.PER_DAY, Decimal("1.42482")),
            UsageComponent(
                schedule=(Window(frozenset(range(7)), (time(16), time(21))),),
                period=Period.PEAK,
                period_label="Peak",
                rate=Decimal("0.36955"),
            ),
            UsageComponent(
                schedule=(Window(frozenset(range(7)), (time(7), time(16))),),
                period=Period.SHOULDER,
                period_label="Shoulder",
                rate=Decimal("0.28950"),
            ),
            UsageComponent(
                schedule=(Window(frozenset(range(7)), (time(21), time(7))),),
                period=Period.OFF_PEAK,
                period_label="Off peak",
                rate=Decimal("0.22900"),
            ),
            UsageComponent(
                register=Register.CONTROLLED_LOAD_1,
                schedule=(Window(frozenset(range(7)), (time(22), time(7))),),
                period=Period.SINGLE,
                rate=Decimal("0.19800"),
            ),
            UsageComponent(
                register=Register.CONTROLLED_LOAD_2,
                schedule=(Window(frozenset(range(7)), (time(22), time(7))),),
                period=Period.SINGLE,
                rate=Decimal("0.19800"),
            ),
            UsageComponent(
                direction=Direction.EXPORT,
                period=Period.SINGLE,
                period_label="Feed-in",
                rate=Decimal("0"),
            ),
        ),
        source=Source(
            type=SourceType.OFFICIAL_FEED,
            feed="au_cdr",
            retrieved_at=datetime(2026, 10, 9, 8, 15, tzinfo=timezone(timedelta(hours=10))),
            raw_sha256="example",
        ),
        confidence=Confidence.HIGH,
        partial=False,
    )
    assert parse_plan((FIXTURES / "origin_ergon.yaml").read_text()) == expected


def test_gas_example_fixture_matches_models() -> None:
    expected = PlanVersion(
        commodity=Commodity.GAS,
        seasons={
            "peak": Season(MonthDay(6, 1), MonthDay(10, 1)),
            "off_peak": Season(MonthDay(10, 1), MonthDay(6, 1)),
        },
        components=(
            FixedComponent(FixedLabel.SUPPLY, FixedUnit.PER_DAY, Decimal("0.9100")),
            UsageComponent(
                quantity_unit="MJ",
                season="peak",
                blocks=Blocks(
                    BlockPeriod.DAY,
                    False,
                    (BlockTier(Decimal("0.0420"), Decimal("100")), BlockTier(Decimal("0.0360"))),
                ),
            ),
            UsageComponent(
                quantity_unit="MJ",
                season="off_peak",
                blocks=Blocks(
                    BlockPeriod.DAY,
                    False,
                    (BlockTier(Decimal("0.0390"), Decimal("100")), BlockTier(Decimal("0.0340"))),
                ),
            ),
        ),
    )
    assert parse_plan((FIXTURES / "gas.json").read_text()) == expected


def test_water_example_fixture_matches_models() -> None:
    expected = PlanVersion(
        commodity=Commodity.WATER,
        billing=Billing((BillingFrequency.P3M,)),
        components=(
            FixedComponent(FixedLabel.WATER_ACCESS, FixedUnit.PER_QUARTER, Decimal("75.20")),
            FixedComponent(FixedLabel.SEWERAGE_ACCESS, FixedUnit.PER_QUARTER, Decimal("165.40")),
            UsageComponent(
                quantity_unit="kL",
                blocks=Blocks(
                    BlockPeriod.QUARTER,
                    True,
                    (BlockTier(Decimal("1.62"), Decimal("60")), BlockTier(Decimal("2.10"))),
                ),
            ),
            UsageComponent(quantity_unit="kL", rate=Decimal("3.29"), label="bulk_water"),
            UsageComponent(
                register=Register.SEWERAGE,
                quantity_unit="kL",
                rate=Decimal("1.92"),
                quantity=DerivedQuantity(Register.GENERAL, Decimal("0.85")),
            ),
        ),
    )
    assert parse_plan((FIXTURES / "water.json").read_text()) == expected


@pytest.mark.parametrize(
    ("data", "path"),
    [
        (
            {"commodity": "gas", "components": [{"kind": "usage", "rate": 0.25}]},
            "$.components[0].rate",
        ),
        (
            {"commodity": "gas", "components": [{"kind": "mystery"}]},
            "$.components[0].kind",
        ),
        ({"commodity": "gas", "unexpected": True}, "$.unexpected"),
        (
            {
                "commodity": "gas",
                "components": [{"kind": "usage", "schedule": "missing"}],
            },
            "$.components[0].schedule",
        ),
        (
            {
                "commodity": "gas",
                "components": [
                    {"kind": "usage", "schedule": {"days": "all", "time": ["24:00", "01:00"]}}
                ],
            },
            "$.components[0].schedule.time[0]",
        ),
        (
            {"commodity": "gas", "seasons": {"x": {"months": [1, 2, 4]}}},
            "$.seasons.x.months",
        ),
    ],
)
def test_parse_errors_identify_exact_path(data: dict[str, Any], path: str) -> None:
    with pytest.raises(ParseError) as raised:
        parse_plan(data)
    assert raised.value.path == path
    assert str(raised.value).startswith(path + ":")


def test_json_decimal_numbers_are_parsed_without_floats() -> None:
    plan = parse_plan('{"commodity":"gas","components":[{"kind":"usage","rate":0.1234}]}')
    assert plan.components[0].rate == Decimal("0.1234")
    assert isinstance(plan.components[0].rate, Decimal)


def test_schedule_variants_and_day_shorthand() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "schedules": {
                "work": [{"days": "weekdays", "time": ["09:00", "24:00"]}],
                "weekend": {"days": "weekends", "time": ["00:00", "12:00"]},
            },
            "components": [
                {"kind": "usage", "schedule": "work"},
                {"kind": "usage", "schedule": {"days": ["mon", "sun"], "time": ["21:00", "01:00"]}},
                {"kind": "usage", "schedule": [{"days": "all", "time": ["00:00", "24:00"]}]},
            ],
        }
    )
    assert plan.schedules["work"][0].days == frozenset(range(5))
    assert plan.schedules["work"][0].time == (time(9), time.min)
    assert plan.schedules["weekend"][0].days == frozenset({5, 6})
    assert plan.components[0].schedule == plan.schedules["work"]
    assert plan.components[1].schedule[0].days == frozenset({0, 6})
    assert plan.components[2].schedule[0].time == (time.min, time.min)


def test_season_month_shorthand_normalizes_contiguous_wrap_and_full_year() -> None:
    plan = parse_plan(
        {
            "commodity": "gas",
            "seasons": {
                "warm": {"months": [11, 12, 1, 2, 3]},
                "year": {"months": list(range(1, 13))},
            },
        }
    )
    assert plan.seasons["warm"] == Season(MonthDay(11, 1), MonthDay(4, 1))
    assert plan.seasons["year"] == Season(MonthDay(1, 1), MonthDay(1, 1))


def test_parse_all_component_shapes_and_extensions() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "bundle_id": "bundle",
            "network_tariff_ref": "network",
            "pricing_model": "pass_through",
            "source": {"type": "community", "custom": {"value": 1}},
            "notes": [{"custom": "accepted"}],
            "unmapped_features": ["also accepted"],
            "components": [
                {"kind": "fixed", "label": "other", "unit": "per_year", "rate": 7},
                {
                    "kind": "usage",
                    "rate": {
                        "entity": "sensor.rate",
                        "multiplier": "2",
                        "adder": "-0.1",
                        "unit_scale": "0.001",
                    },
                    "quantity": {"from": "general", "factor": "0.85"},
                },
                {
                    "kind": "demand",
                    "method": "top_n_avg",
                    "interval_minutes": 30,
                    "top_n": 3,
                    "measure": "kVA",
                    "threshold": "35",
                    "reset": "billing_period",
                    "unit": "per_kva_per_month",
                    "rate": "12.50",
                },
                {
                    "kind": "discount",
                    "applies_to": ["usage.import"],
                    "percent": 10,
                    "conditional": None,
                },
                {"kind": "incentive", "label": "credit", "value": "10", "details": {"code": "x"}},
            ],
        }
    )
    assert plan.source == Source(type=SourceType.COMMUNITY, extra={"custom": {"value": 1}})
    assert plan.notes == ({"custom": "accepted"},)
    assert plan.components == (
        FixedComponent(FixedLabel.OTHER, FixedUnit.PER_YEAR, Decimal(7)),
        UsageComponent(
            rate=RateSource("sensor.rate", Decimal("2"), Decimal("-0.1"), Decimal("0.001")),
            quantity=DerivedQuantity(Register.GENERAL, Decimal("0.85")),
        ),
        DemandComponent(
            DemandMethod.TOP_N_AVG,
            DemandMeasure.KVA,
            DemandUnit.PER_KVA_PER_MONTH,
            Decimal("12.50"),
            interval_minutes=30,
            top_n=3,
            threshold=Decimal("35"),
            reset=DemandReset.BILLING_PERIOD,
        ),
        DiscountComponent(("usage.import",), Decimal(10)),
        IncentiveComponent("credit", value="10", details={"code": "x"}),
    )


@pytest.mark.parametrize(
    ("data", "path"),
    [
        ({"commodity": "gas", "tax": {"inclusive": True, "rate": 0.1}}, "$.tax.rate"),
        (
            {
                "commodity": "gas",
                "components": [
                    {
                        "kind": "usage",
                        "blocks": {"period": "day", "tiers": [{"up_to": 2.2, "rate": "1"}]},
                    }
                ],
            },
            "$.components[0].blocks.tiers[0].up_to",
        ),
        (
            {
                "commodity": "gas",
                "components": [{"kind": "usage", "rate": {"entity": "sensor.x", "adder": 0.1}}],
            },
            "$.components[0].rate.adder",
        ),
        (
            {"commodity": "gas", "source": {"retrieved_at": "2026-01-01T00:00:00"}},
            "$.source.retrieved_at",
        ),
    ],
)
def test_nested_values_are_strictly_typed(data: dict[str, Any], path: str) -> None:
    with pytest.raises(ParseError) as raised:
        parse_plan(data)
    assert raised.value.path == path


def test_parser_accepts_mapping_subclasses_and_json_null_is_rejected_as_root() -> None:
    from collections import UserDict

    assert parse_plan(UserDict({"commodity": "water"})).commodity is Commodity.WATER
    with pytest.raises(ParseError, match=r"^\$: expected an object$"):
        parse_plan("null")


def test_invalid_date_and_non_finite_json_values_are_rejected() -> None:
    with pytest.raises(ParseError) as invalid_date:
        parse_plan({"commodity": "gas", "effective": {"from": "2026-02-30"}})
    assert invalid_date.value.path == "$.effective.from"
    with pytest.raises(ParseError, match="invalid JSON numeric constant NaN"):
        parse_plan('{"commodity":"gas","tax":{"inclusive":true,"rate":NaN}}')


def test_frozen_models_and_month_day_validation() -> None:
    plan = PlanVersion(Commodity.GAS)
    with pytest.raises(FrozenInstanceError):
        plan.commodity = Commodity.WATER  # type: ignore[misc]
    assert MonthDay(2, 29) == MonthDay(2, 29)
    with pytest.raises(ValueError):
        MonthDay(2, 30)


@pytest.mark.parametrize(
    ("data", "path"),
    [
        (1, "$"),
        ({"commodity": "gas", 1: "bad"}, "$"),
        ({}, "$.commodity"),
        ({"commodity": "gas", "components": "not a list"}, "$.components"),
        ({"commodity": 1}, "$.commodity"),
        ({"commodity": "invalid"}, "$.commodity"),
        ({"commodity": "gas", "tax": {"inclusive": 1, "rate": "0.1"}}, "$.tax.inclusive"),
        (
            {
                "commodity": "gas",
                "components": [
                    {
                        "kind": "demand",
                        "method": "max_instant",
                        "measure": "kW",
                        "unit": "per_kw_per_month",
                        "rate": "1",
                        "interval_minutes": "30",
                    }
                ],
            },
            "$.components[0].interval_minutes",
        ),
        (
            {"commodity": "gas", "components": [{"kind": "usage", "rate": True}]},
            "$.components[0].rate",
        ),
        (
            {"commodity": "gas", "components": [{"kind": "usage", "rate": "not-a-number"}]},
            "$.components[0].rate",
        ),
        (
            {"commodity": "gas", "components": [{"kind": "usage", "rate": "NaN"}]},
            "$.components[0].rate",
        ),
        (
            {"commodity": "gas", "effective": {"from": datetime(2026, 1, 1)}},
            "$.effective.from",
        ),
        (
            {"commodity": "gas", "effective": {"from": "2026-W01-1"}},
            "$.effective.from",
        ),
        (
            {"commodity": "gas", "effective": {"from": "not a date"}},
            "$.effective.from",
        ),
        (
            {"commodity": "gas", "source": {"retrieved_at": "not a datetime"}},
            "$.source.retrieved_at",
        ),
        ({"commodity": "gas", "source": {"retrieved_at": 1}}, "$.source.retrieved_at"),
        (
            {"commodity": "gas", "seasons": {"s": {"from": "2-01", "to": "03-01"}}},
            "$.seasons.s.from",
        ),
        (
            {"commodity": "gas", "seasons": {"s": {"from": "02-30", "to": "03-01"}}},
            "$.seasons.s.from",
        ),
        ({"commodity": "gas", "seasons": {"s": {"months": [1], "from": "01-01"}}}, "$.seasons.s"),
        ({"commodity": "gas", "seasons": {"s": {"months": [13]}}}, "$.seasons.s.months[0]"),
        ({"commodity": "gas", "seasons": {"s": {"months": [1, 1]}}}, "$.seasons.s.months[1]"),
        ({"commodity": "gas", "seasons": {"s": {"months": []}}}, "$.seasons.s.months"),
        ({"commodity": "gas", "seasons": {"s": {"from": "01-01"}}}, "$.seasons.s"),
        (
            {
                "commodity": "gas",
                "components": [{"kind": "usage", "schedule": {"time": ["00:00"]}}],
            },
            "$.components[0].schedule.time",
        ),
        (
            {
                "commodity": "gas",
                "components": [
                    {"kind": "usage", "schedule": {"days": ["funday"], "time": ["00:00", "01:00"]}}
                ],
            },
            "$.components[0].schedule.days[0]",
        ),
        (
            {
                "commodity": "gas",
                "components": [
                    {"kind": "usage", "schedule": {"days": [1], "time": ["00:00", "01:00"]}}
                ],
            },
            "$.components[0].schedule.days[0]",
        ),
        ({"commodity": "gas", "components": [3]}, "$.components[0]"),
        ({"commodity": "gas", "components": [{}]}, "$.components[0].kind"),
        ({"commodity": "gas", "components": [{"kind": 1}]}, "$.components[0].kind"),
        (
            {"commodity": "gas", "components": [{"kind": "incentive", "value": 1.5}]},
            "$.components[0].value",
        ),
        (
            {"commodity": "gas", "components": [{"kind": "incentive", "value": []}]},
            "$.components[0].value",
        ),
        (
            {"commodity": "gas", "components": [{"kind": "incentive", "details": []}]},
            "$.components[0].details",
        ),
        ({"commodity": "gas", "source": []}, "$.source"),
        ({"commodity": "gas", "source": {1: "not a string"}}, "$.source"),
        ({"commodity": "gas", "schedules": []}, "$.schedules"),
        ({"commodity": "gas", "schedules": {1: []}}, "$.schedules"),
        ({"commodity": "gas", "seasons": []}, "$.seasons"),
        ({"commodity": "gas", "seasons": {1: {"from": "01-01", "to": "02-01"}}}, "$.seasons"),
        ({"commodity": "gas", "notes": "not a list"}, "$.notes"),
        ({"commodity": "gas", "partial": 1}, "$.partial"),
    ],
)
def test_invalid_shapes_and_values_report_paths(data: Any, path: str) -> None:
    with pytest.raises(ParseError) as raised:
        parse_plan(data)
    assert raised.value.path == path


def test_monthday_day_name_and_empty_optional_fields() -> None:
    plan = parse_plan(
        {
            "commodity": "gas",
            "effective": {"from": "2026-01-01", "to": None},
            "schedules": {"empty": []},
            "components": [
                {"kind": "usage", "schedule": {"days": "mon", "time": ["00:00", "01:00"]}}
            ],
        }
    )
    assert plan.effective == Effective(date(2026, 1, 1), None)
    assert plan.schedules["empty"] == ()
    assert plan.components[0].schedule == (Window(frozenset({0}), (time.min, time(1))),)


def test_yaml_parser_error_and_missing_optional_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ParseError) as malformed:
        parse_plan("commodity: [")
    assert malformed.value.path == "$"
    real_import = builtins.__import__

    def no_yaml(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml":
            raise ImportError("yaml deliberately hidden")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    with pytest.raises(ParseError, match=r"install tariff-core\[yaml\]"):
        parse_plan("commodity: gas")
