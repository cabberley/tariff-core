"""Tests for time-varying and dynamic tariff rates."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tariff_core import forecast, parse_plan, rate_at
from tariff_core.adapters.cdr import from_cdr

FIXTURES = Path(__file__).parent / "fixtures" / "plans" / "cdr"
BRISBANE = ZoneInfo("Australia/Brisbane")


def _origin_plan():
    raw = json.loads((FIXTURES / "ORI1161031MRE3@EME.json").read_text())
    return from_cdr(raw, retrieved_at=datetime(2026, 10, 9, tzinfo=UTC))[0]


@pytest.mark.parametrize(
    ("local_when", "rate", "period", "label", "local_change", "next_rate"),
    [
        ("2026-10-09T08:27", "0.22767", "shoulder", "Night", "2026-10-09T11:00", "0.16829"),
        ("2026-10-09T11:00", "0.16829", "off_peak", "Day", "2026-10-09T16:00", "0.36955"),
        ("2026-10-09T15:59", "0.16829", "off_peak", "Day", "2026-10-09T16:00", "0.36955"),
        ("2026-10-09T16:00", "0.36955", "peak", "Peak", "2026-10-09T21:00", "0.22767"),
        ("2026-10-09T21:00", "0.22767", "shoulder", "Night", "2026-10-10T11:00", "0.16829"),
    ],
)
def test_origin_rate_at_matches_issue_table(
    local_when: str,
    rate: str,
    period: str,
    label: str,
    local_change: str,
    next_rate: str,
) -> None:
    result = rate_at(_origin_plan(), datetime.fromisoformat(local_when).replace(tzinfo=BRISBANE))

    assert result.rate == Decimal(rate)
    assert result.period == period
    assert result.period_label == label
    assert result.next_change == datetime.fromisoformat(local_change).replace(
        tzinfo=BRISBANE
    ).astimezone(UTC)
    assert result.next_rate == Decimal(next_rate)


def test_flat_register_and_export_have_no_next_change() -> None:
    plan = _origin_plan()
    when = datetime(2026, 10, 9, 8, 27, tzinfo=BRISBANE)

    controlled_load = rate_at(plan, when, register="controlled_load_1")
    export = rate_at(plan, when, direction="export")

    assert controlled_load.rate == Decimal("0.15148")
    assert controlled_load.next_change is None
    assert export.rate == Decimal("0")
    assert export.next_change is None


def test_forecast_merges_to_seven_daily_rate_slots() -> None:
    plan = _origin_plan()
    start = datetime(2026, 10, 9, tzinfo=BRISBANE)
    end = datetime(2026, 10, 11, tzinfo=BRISBANE)

    slots = forecast(plan, start, end)

    assert [slot.period for slot in slots] == [
        "shoulder",
        "off_peak",
        "peak",
        "shoulder",
        "off_peak",
        "peak",
        "shoulder",
    ]
    assert [slot.rate for slot in slots] == [
        Decimal("0.22767"),
        Decimal("0.16829"),
        Decimal("0.36955"),
        Decimal("0.22767"),
        Decimal("0.16829"),
        Decimal("0.36955"),
        Decimal("0.22767"),
    ]
    assert slots[0].start == start
    assert slots[-1].end == end


def test_dynamic_rate_missing_value_and_transform() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "timezone": "UTC",
            "components": [
                {
                    "kind": "usage",
                    "rate": {
                        "entity": "sensor.price",
                        "multiplier": "2",
                        "adder": "1",
                        "unit_scale": "0.001",
                    },
                }
            ],
        }
    )
    when = datetime(2026, 1, 1, tzinfo=UTC)

    missing = rate_at(plan, when)
    resolved = rate_at(plan, when, resolved={"sensor.price": Decimal("1000")})

    assert missing.rate is None
    assert missing.is_dynamic
    assert resolved.rate == Decimal("2.001")
    assert resolved.is_dynamic
    assert resolved.next_change is None


def test_block_pricing_exposes_first_tier_rate() -> None:
    plan = parse_plan(
        {
            "commodity": "gas",
            "timezone": "UTC",
            "components": [
                {
                    "kind": "usage",
                    "blocks": {
                        "period": "day",
                        "tiers": [{"up_to": "10", "rate": "0.2"}, {"rate": "0.1"}],
                    },
                }
            ],
        }
    )

    result = rate_at(plan, datetime(2026, 1, 1, tzinfo=UTC))

    assert result.rate == Decimal("0.2")
    assert result.blocked


def test_season_boundary_is_reported_as_next_change() -> None:
    plan = parse_plan(
        {
            "commodity": "gas",
            "timezone": "UTC",
            "seasons": {
                "winter": {"from": "06-01", "to": "10-01"},
                "summer": {"from": "10-01", "to": "06-01"},
            },
            "components": [
                {"kind": "usage", "season": "winter", "rate": "1"},
                {"kind": "usage", "season": "summer", "rate": "2"},
            ],
        }
    )

    result = rate_at(plan, datetime(2026, 9, 30, 23, 59, tzinfo=UTC))

    assert result.rate == Decimal("1")
    assert result.next_change == datetime(2026, 10, 1, tzinfo=UTC)
    assert result.next_rate == Decimal("2")


def test_as_weekend_holiday_uses_weekend_component() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "timezone": "Australia/Brisbane",
            "holidays": {"calendar": "AU-QLD", "treatment": "as_weekend"},
            "schedules": {
                "weekday": [{"days": "weekdays", "time": ["00:00", "24:00"]}],
                "weekend": [{"days": "weekends", "time": ["00:00", "24:00"]}],
            },
            "components": [
                {"kind": "usage", "schedule": "weekday", "rate": "1"},
                {"kind": "usage", "schedule": "weekend", "rate": "2"},
            ],
        }
    )

    result = rate_at(plan, datetime(2026, 10, 5, 12, tzinfo=BRISBANE))

    assert result.rate == Decimal("2")


@pytest.mark.parametrize(
    ("local_when", "expected_utc"),
    [
        ("2026-10-04T15:59:00+11:00", "2026-10-04T05:00:00+00:00"),
        ("2026-04-05T15:59:00+10:00", "2026-04-05T06:00:00+00:00"),
    ],
)
def test_next_change_uses_local_clock_across_dst(local_when: str, expected_utc: str) -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "timezone": "Australia/Sydney",
            "schedules": {
                "peak": [{"days": "all", "time": ["16:00", "21:00"]}],
            },
            "components": [
                {"kind": "usage", "rate": "1"},
                {"kind": "usage", "schedule": "peak", "period": "peak", "rate": "2"},
            ],
        }
    )

    result = rate_at(plan, datetime.fromisoformat(local_when))

    assert result.next_change == datetime.fromisoformat(expected_utc)


def test_forecast_splits_dynamic_rate_series_boundaries() -> None:
    plan = parse_plan(
        {
            "commodity": "electricity",
            "timezone": "UTC",
            "components": [{"kind": "usage", "rate": {"entity": "sensor.price"}}],
        }
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    middle = datetime(2026, 1, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 1, 2, tzinfo=UTC)

    slots = forecast(
        plan,
        start,
        end,
        resolved_series=[(start, middle, Decimal("0.1")), (middle, end, Decimal("0.2"))],
    )

    assert [(slot.start, slot.end, slot.rate) for slot in slots] == [
        (start, middle, Decimal("0.1")),
        (middle, end, Decimal("0.2")),
    ]


def test_forecast_refreshes_after_rate_lookup_lookahead() -> None:
    plan = parse_plan(
        {
            "commodity": "gas",
            "timezone": "UTC",
            "seasons": {
                "winter": {"from": "01-01", "to": "01-10"},
                "summer": {"from": "01-10", "to": "01-01"},
            },
            "components": [
                {"kind": "usage", "season": "winter", "rate": "1"},
                {"kind": "usage", "season": "summer", "rate": "2"},
            ],
        }
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    transition = datetime(2026, 1, 10, tzinfo=UTC)
    end = datetime(2026, 1, 12, tzinfo=UTC)

    slots = forecast(plan, start, end)

    assert [(slot.start, slot.end, slot.rate) for slot in slots] == [
        (start, transition, Decimal("1")),
        (transition, end, Decimal("2")),
    ]
