"""Tests for Australian CDR plan conversion."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tariff_core import (
    Commodity,
    DemandComponent,
    Direction,
    FixedComponent,
    IncentiveComponent,
    Period,
    Register,
    UsageComponent,
    content_hash,
)
from tariff_core.adapters.cdr import from_cdr

FIXTURES = Path(__file__).parent / "fixtures" / "plans" / "cdr"
RETRIEVED_AT = datetime(2026, 10, 9, tzinfo=UTC)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.json")))
def test_cdr_fixtures_convert_deterministically(fixture: Path) -> None:
    raw = json.loads(fixture.read_text())
    first = from_cdr(raw, retrieved_at=RETRIEVED_AT, url="https://example.test/plan")
    second = from_cdr(raw, retrieved_at=RETRIEVED_AT, url="https://example.test/plan")

    assert len(first) == 1
    assert content_hash(first[0]) == content_hash(second[0])
    assert first[0].source is not None
    assert first[0].source.raw_sha256 is not None


def test_origin_ergon_cdr_tariffs_are_normalised() -> None:
    plan = from_cdr(
        _fixture("ORI1161031MRE3@EME.json"),
        retrieved_at=RETRIEVED_AT,
        url="https://example.test/origin",
    )[0]

    assert plan.plan_id == "au:cdr:ORI1161031MRE3@EME"
    assert plan.commodity is Commodity.ELECTRICITY
    supply = next(
        component
        for component in plan.components
        if isinstance(component, FixedComponent) and component.register is None
    )
    assert supply.rate == Decimal("1.42482")

    expected = {
        Period.PEAK: ("16:00", "21:00", "0.36955", "Peak"),
        Period.OFF_PEAK: ("11:00", "16:00", "0.16829", "Day"),
        Period.SHOULDER: ("21:00", "11:00", "0.22767", "Night"),
    }
    for period, (start, end, rate, label) in expected.items():
        component = next(
            component
            for component in plan.components
            if isinstance(component, UsageComponent)
            and component.direction is Direction.IMPORT
            and component.period is period
        )
        assert component.rate == Decimal(rate)
        assert component.period_label == label
        assert component.schedule is not None
        assert component.schedule[0].time[0].strftime("%H:%M") == start
        assert component.schedule[0].time[1].strftime("%H:%M") == end

    for register, rate in (
        (Register.CONTROLLED_LOAD_1, "0.15148"),
        (Register.CONTROLLED_LOAD_2, "0.1524"),
    ):
        component = next(
            component
            for component in plan.components
            if isinstance(component, UsageComponent) and component.register is register
        )
        assert component.rate == Decimal(rate)

    export = next(
        component
        for component in plan.components
        if isinstance(component, UsageComponent) and component.direction is Direction.EXPORT
    )
    assert export.rate == Decimal("0")
    assert any("Non-Solar Eligibility" in note for note in plan.notes)
    assert sum(isinstance(component, IncentiveComponent) for component in plan.components) == 1


def test_cdr_gas_is_priced_in_mj_blocks() -> None:
    plan = from_cdr(
        _fixture("ORI931606MBG4@EME.json"),
        retrieved_at=RETRIEVED_AT,
        url="https://example.test/gas",
    )[0]

    assert plan.commodity is Commodity.GAS
    usages = [component for component in plan.components if isinstance(component, UsageComponent)]
    assert usages
    assert all(component.quantity_unit == "MJ" for component in usages)
    assert usages[0].blocks is not None
    assert usages[0].blocks.tiers[0].up_to == Decimal("200.01")


def test_cdr_demand_and_time_varying_feed_in_are_mapped() -> None:
    demand_plan = from_cdr(
        _fixture("ORI988028MBE7@EME.json"),
        retrieved_at=RETRIEVED_AT,
        url="https://example.test/demand",
    )[0]
    assert any(isinstance(component, DemandComponent) for component in demand_plan.components)

    feed_in_plan = from_cdr(
        _fixture("ORI1202131MRE2@EME.json"),
        retrieved_at=RETRIEVED_AT,
        url="https://example.test/feed-in",
    )[0]
    exports = [
        component
        for component in feed_in_plan.components
        if isinstance(component, UsageComponent) and component.direction is Direction.EXPORT
    ]
    assert len(exports) == 2
    assert all(isinstance(component.rate, Decimal) and component.rate < 0 for component in exports)
    assert all(component.schedule for component in exports)
