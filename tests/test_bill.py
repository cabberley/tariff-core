"""Golden bills and regression coverage for interval billing."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from tariff_core import (
    BillingPeriod,
    Commodity,
    Contract,
    ContractSegment,
    Conversion,
    Interval,
    parse_plan,
)
from tariff_core.adapters.cdr import from_cdr
from tariff_core.engine.bill import bill

FIXTURES = Path(__file__).parent / "fixtures" / "plans"
BRISBANE = ZoneInfo("Australia/Brisbane")


def _origin_plan():
    raw = json.loads((FIXTURES / "cdr" / "ORI1161031MRE3@EME.json").read_text())
    return from_cdr(raw, retrieved_at=datetime(2026, 10, 9, tzinfo=UTC))[0]


def test_origin_ergon_daily_bill_matches_golden_numbers() -> None:
    """Expected line items use the Issue 8 quantities and the checked-in Origin CDR rates."""
    plan = _origin_plan()
    start = datetime(2026, 10, 7, tzinfo=BRISBANE)
    intervals = []
    for index in range(48):
        interval_start = start + timedelta(minutes=30 * index)
        hour = interval_start.hour
        controlled_load = hour in {22, 23, 0, 1}
        export = Decimal("0.05") if 10 <= hour < 14 else Decimal(0)
        intervals.append(
            Interval(
                end=interval_start + timedelta(minutes=30),
                duration=timedelta(minutes=30),
                register="general",
                unit="kWh",
                import_qty=Decimal("0.25"),
                export_qty=export,
            )
        )
        if controlled_load:
            intervals[-1] = replace(intervals[-1], export_qty=Decimal(0))
            intervals.append(
                Interval(
                    end=interval_start + timedelta(minutes=30),
                    duration=timedelta(minutes=30),
                    register="controlled_load_1",
                    unit="kWh",
                    import_qty=Decimal("0.5"),
                )
            )

    result = bill(
        plan,
        intervals,
        period=BillingPeriod(start, start + timedelta(days=1)),
    )

    assert [
        (line.label, line.quantity, line.rate, line.amount, line.type) for line in result.line_items
    ] == [
        ("Supply", Decimal(1), Decimal("1.42482"), Decimal("1.42482"), "cost"),
        ("Peak import", Decimal("2.50"), Decimal("0.36955"), Decimal("0.923875"), "cost"),
        ("Day import", Decimal("2.50"), Decimal("0.16829"), Decimal("0.420725"), "cost"),
        ("Night import", Decimal("7.00"), Decimal("0.22767"), Decimal("1.593690"), "cost"),
        ("Controlled load 1", Decimal("4.0"), Decimal("0.15148"), Decimal("0.605920"), "cost"),
        ("Export", Decimal("0.40"), Decimal(0), Decimal(0), "credit"),
    ]
    assert result.total == Decimal("4.969030")
    assert result.total_rounded == Decimal("4.97")


def test_gas_bill_uses_dated_contract_conversion() -> None:
    """Expected tiers follow the schema gas example and the Issue 8 printed conversion values."""
    plan = replace(parse_plan((FIXTURES / "gas.json").read_text()), timezone="Australia/Brisbane")
    start = datetime(2026, 10, 7, tzinfo=BRISBANE)
    contract = Contract(
        Commodity.GAS,
        conversions=(
            Conversion(
                from_=datetime(2026, 7, 1).date(),
                register="general",
                meter_unit="m3",
                billed_unit="MJ",
                heating_value=Decimal("38.62"),
                correction_factor=Decimal("0.9823"),
            ),
        ),
    )
    result = bill(
        plan,
        [
            Interval(
                end=start + timedelta(days=1),
                duration=timedelta(days=1),
                register="general",
                unit="m3",
                import_qty=Decimal(3),
            )
        ],
        period=BillingPeriod(start, start + timedelta(days=1), contract),
    )

    assert [(line.label, line.quantity, line.rate, line.amount) for line in result.line_items] == [
        ("Supply", Decimal(1), Decimal("0.9100"), Decimal("0.9100")),
        ("Usage tier 1", Decimal("100"), Decimal("0.0390"), Decimal("3.9000")),
        (
            "Usage tier 2",
            Decimal("13.809278"),
            Decimal("0.0340"),
            Decimal("0.4695154520"),
        ),
    ]
    assert result.total == Decimal("5.2795154520")
    assert result.total_rounded == Decimal("5.28")


def test_water_bill_matches_quarterly_golden_numbers() -> None:
    """Expected charges follow the schema water example and the 92-day Issue 8 period."""
    plan = replace(parse_plan((FIXTURES / "water.json").read_text()), timezone="Australia/Brisbane")
    start = datetime(2026, 7, 1, tzinfo=BRISBANE)
    end = datetime(2026, 10, 1, tzinfo=BRISBANE)
    result = bill(
        plan,
        [
            Interval(
                end=end,
                duration=end - start,
                register="general",
                unit="kL",
                import_qty=Decimal(50),
            )
        ],
        period=BillingPeriod(start, end),
    )

    assert [(line.label, line.quantity, line.rate) for line in result.line_items] == [
        ("Water access", Decimal(92), Decimal("75.20") * 4 / 365),
        ("Sewerage access", Decimal(92), Decimal("165.40") * 4 / 365),
        ("Water usage tier 1", Decimal(50), Decimal("1.62")),
        ("Bulk water", Decimal(50), Decimal("3.29")),
        ("Sewerage", Decimal("42.50"), Decimal("1.92")),
    ]
    assert result.total == Decimal("569.6775342465753424657534247")
    assert result.total_rounded == Decimal("569.68")


def test_billing_is_repeatable_and_demand_uses_interval_average() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "timezone": "UTC",
            "components": [
                {
                    "kind": "demand",
                    "method": "max_interval_avg",
                    "measure": "kW",
                    "unit": "per_kw_per_month",
                    "rate": "12.50",
                    "reset": "billing_period",
                    "schedule": [{"days": "all", "time": ["16:00", "21:00"]}],
                }
            ],
        }
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    intervals = []
    for day in range(31):
        day_start = start + timedelta(days=day)
        intervals.extend(
            [
                Interval(
                    end=day_start + timedelta(hours=16),
                    duration=timedelta(minutes=30),
                    register="general",
                    unit="Wh",
                    import_qty=Decimal("2500"),
                ),
                Interval(
                    end=day_start + timedelta(hours=17),
                    duration=timedelta(minutes=30),
                    register="general",
                    unit="Wh",
                    import_qty=Decimal("1500"),
                ),
                Interval(
                    end=day_start + timedelta(hours=18, minutes=30),
                    duration=timedelta(minutes=30),
                    register="general",
                    unit="Wh",
                    import_qty=Decimal("500"),
                ),
            ]
        )
    billing_period = BillingPeriod(start, datetime(2026, 2, 1, tzinfo=UTC))

    first = bill(plan, intervals, period=billing_period)

    assert first == bill(plan, intervals, period=billing_period)
    assert first.line_items[0].quantity == Decimal(3)
    assert first.line_items[0].amount == Decimal("37.50")
    assert first.total_rounded == Decimal("37.50")


def test_split_billing_periods_sum_to_full_period_bill() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "timezone": "UTC",
            "components": [
                {"kind": "fixed", "label": "supply", "unit": "per_day", "rate": "1"},
                {"kind": "usage", "rate": "0.1"},
            ],
        }
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    middle = start + timedelta(hours=12)
    end = start + timedelta(days=1)
    intervals = [
        Interval(
            end=middle,
            duration=timedelta(hours=12),
            register="general",
            unit="kWh",
            import_qty=Decimal(1),
        ),
        Interval(
            end=end,
            duration=timedelta(hours=12),
            register="general",
            unit="kWh",
            import_qty=Decimal(1),
        ),
    ]

    whole = bill(plan, intervals, period=BillingPeriod(start, end))
    first_half = bill(plan, intervals[:1], period=BillingPeriod(start, middle))
    second_half = bill(plan, intervals[1:], period=BillingPeriod(middle, end))

    assert whole.total == first_half.total + second_half.total


def test_positive_export_rate_is_charged_as_a_cost() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "timezone": "UTC",
            "components": [{"kind": "usage", "direction": "export", "rate": "0.05"}],
        }
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    result = bill(
        plan,
        [
            Interval(
                end=start + timedelta(minutes=30),
                duration=timedelta(minutes=30),
                register="general",
                unit="kWh",
                import_qty=Decimal(0),
                export_qty=Decimal(2),
            )
        ],
        period=BillingPeriod(start, start + timedelta(minutes=30)),
    )

    assert result.line_items[0].type == "cost"
    assert result.line_items[0].amount == Decimal("0.10")


def test_long_interval_splits_at_contract_segment_boundary() -> None:
    first_plan = parse_plan(
        {
            "commodity": "gas",
            "timezone": "Australia/Brisbane",
            "components": [{"kind": "usage", "quantity_unit": "MJ", "rate": "1"}],
        }
    )
    second_plan = replace(
        first_plan, components=(replace(first_plan.components[0], rate=Decimal(2)),)
    )
    contract = Contract(
        Commodity.GAS,
        segments=(
            ContractSegment(date(2026, 1, 1), date(2026, 1, 2), first_plan),
            ContractSegment(date(2026, 1, 2), None, second_plan),
        ),
    )
    start = datetime(2026, 1, 1, tzinfo=BRISBANE)
    end = start + timedelta(days=2)
    result = bill(
        first_plan,
        [
            Interval(
                end=end,
                duration=end - start,
                register="general",
                unit="MJ",
                import_qty=Decimal(10),
            )
        ],
        period=BillingPeriod(start, end, contract),
    )

    assert result.total == Decimal(15)
    assert result.line_items[0].quantity == Decimal(10)
    assert result.line_items[0].rate == Decimal("1.5")
    assert any("split" in warning for warning in result.warnings)


def test_dynamic_rates_discounts_and_incentives() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "timezone": "UTC",
            "components": [
                {"kind": "usage", "rate": {"entity": "sensor.price"}},
                {"kind": "discount", "applies_to": ["usage.import"], "percent": "10"},
                {
                    "kind": "discount",
                    "applies_to": ["usage.import"],
                    "percent": "10",
                    "conditional": "pay_on_time",
                },
                {"kind": "incentive", "label": "welcome credit"},
            ],
        }
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    interval = Interval(
        end=start + timedelta(hours=1),
        duration=timedelta(hours=1),
        register="general",
        unit="kWh",
        import_qty=Decimal(2),
    )
    period = BillingPeriod(start, start + timedelta(hours=1))

    def resolver(_entity: str, _at: datetime) -> Decimal:
        return Decimal("0.2")

    included = bill(plan, [interval], period=period, resolved_rates=resolver)
    missing = bill(plan, [interval], period=period, resolved_rates=lambda _entity, _at: None)

    assert [(line.label, line.amount, line.type) for line in included.line_items] == [
        ("Usage", Decimal("0.4"), "cost"),
        ("Discount", Decimal("0.04"), "credit"),
    ]
    assert included.total == Decimal("0.36")
    assert not any("incentive" in line.label.lower() for line in included.line_items)
    assert missing.line_items == ()
    assert missing.warnings == ("Dynamic rate missing for 1 interval(s) on component 0",)


def test_daily_blocks_reset_when_a_long_read_crosses_midnight() -> None:
    plan = parse_plan(
        {
            "commodity": "gas",
            "timezone": "UTC",
            "components": [
                {
                    "kind": "usage",
                    "quantity_unit": "MJ",
                    "blocks": {
                        "period": "day",
                        "prorate": False,
                        "tiers": [{"up_to": "100", "rate": "0.1"}, {"rate": "0.2"}],
                    },
                }
            ],
        }
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=2)
    result = bill(
        plan,
        [
            Interval(
                end=end,
                duration=end - start,
                register="general",
                unit="MJ",
                import_qty=Decimal(300),
            )
        ],
        period=BillingPeriod(start, end),
    )

    assert [(line.quantity, line.amount) for line in result.line_items] == [
        (Decimal(200), Decimal("20.0")),
        (Decimal(100), Decimal("20.0")),
    ]
    assert any("split" in warning for warning in result.warnings)
