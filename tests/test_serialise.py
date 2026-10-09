"""Tests for canonical plan serialisation and content hashing."""

import json
import random
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tariff_core import (
    Commodity,
    Confidence,
    Effective,
    PlanVersion,
    Source,
    UsageComponent,
    Window,
    content_hash,
    dump_plan,
    parse_plan,
    to_dict,
    version_id,
)

FIXTURES = Path(__file__).parent / "fixtures" / "plans"


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*")))
def test_plan_fixtures_round_trip(fixture: Path) -> None:
    plan = parse_plan(fixture.read_text())
    assert parse_plan(dump_plan(plan)) == plan


def test_decimal_format_and_seasons_are_canonical() -> None:
    plan = parse_plan(
        {
            "commodity": "gas",
            "display_name": "Énergie",
            "seasons": {"warm": {"months": [11, 12, 1, 2, 3]}},
            "components": [{"kind": "usage", "rate": "1.5000"}],
        }
    )
    document = to_dict(plan)
    assert document["seasons"] == {"warm": {"from": "11-01", "to": "04-01"}}
    assert document["components"][0]["rate"] == "1.5"
    assert json.loads(dump_plan(plan)) == document
    assert '"display_name":"Énergie"' in dump_plan(plan)


def test_canonical_hash_is_pinned_for_gas_fixture() -> None:
    plan = parse_plan((FIXTURES / "gas.json").read_text())
    assert content_hash(plan) == "c650b3c2dad08a9f6b73d1b2f57d082686c7d20b6aac7661677cc4377b9b54be"
    assert dump_plan(plan) == (
        '{"billing":{"frequencies":[]},"commodity":"gas","components":'
        '[{"kind":"fixed","label":"supply","rate":"0.91","unit":"per_day"},'
        '{"blocks":{"period":"day","prorate":false,"tiers":[{"rate":"0.042","up_to":"100"},'
        '{"rate":"0.036"}]},"direction":"import","kind":"usage","quantity_unit":"MJ",'
        '"register":"general","season":"peak"},{"blocks":{"period":"day","prorate":false,'
        '"tiers":[{"rate":"0.039","up_to":"100"},{"rate":"0.034"}]},"direction":"import",'
        '"kind":"usage","quantity_unit":"MJ","register":"general","season":"off_peak"}],'
        '"customer_type":"residential","kind":"retail","notes":[],"partial":false,'
        '"pricing_model":"bundled","schedules":{},'
        '"seasons":{"off_peak":{"from":"10-01","to":"06-01"},"peak":{"from":"06-01","to":"10-01"}},'
        '"time_basis":"local",'
        '"unmapped_features":[]}'
    )


def test_hash_ignores_source_and_confidence_but_includes_plan_content() -> None:
    plan = parse_plan((FIXTURES / "gas.json").read_text())
    with_metadata = replace(
        plan,
        id="some-version",
        source=Source(
            retrieved_at=datetime(2026, 10, 9, tzinfo=UTC),
            raw_sha256="different",
            extra={"another": "source"},
        ),
        confidence=Confidence.LOW,
    )
    assert content_hash(with_metadata) == content_hash(plan)

    changed_component = replace(plan.components[0], rate=Decimal("0.92"))
    changed = replace(plan, components=(changed_component, *plan.components[1:]))
    assert content_hash(changed) != content_hash(plan)


def test_hash_changes_for_window_block_threshold_and_unit() -> None:
    plan = parse_plan((FIXTURES / "gas.json").read_text())
    usage = plan.components[1]
    assert isinstance(usage, UsageComponent)
    assert usage.blocks is not None

    def with_usage(updated: Any) -> PlanVersion:
        return replace(plan, components=(plan.components[0], updated, *plan.components[2:]))

    changed_threshold = replace(
        usage,
        blocks=replace(
            usage.blocks,
            tiers=(
                replace(usage.blocks.tiers[0], up_to=Decimal("101")),
                *usage.blocks.tiers[1:],
            ),
        ),
    )
    changed_unit = replace(usage, quantity_unit="GJ")
    changed_window = replace(
        usage,
        schedule=(Window(frozenset({0}), (time(8), time(9))),),
    )
    for changed in (changed_threshold, changed_unit, changed_window):
        assert content_hash(with_usage(changed)) != content_hash(plan)


def _reorder(value: Any, seed: int) -> Any:
    rng = random.Random(seed)
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {key: _reorder(item, rng.randrange(2**32)) for key, item in items}
    if isinstance(value, list):
        return [_reorder(item, rng.randrange(2**32)) for item in value]
    return value


@given(seed=st.integers())
def test_hash_is_independent_of_input_mapping_order(seed: int) -> None:
    source = json.loads((FIXTURES / "water.json").read_text())
    reordered = _reorder(source, seed)
    assert content_hash(parse_plan(reordered)) == content_hash(parse_plan(source))


def test_version_id_uses_plan_date_and_hash_prefix() -> None:
    plan = PlanVersion(
        commodity=Commodity.GAS,
        plan_id="au:community:sample",
        effective=Effective(from_=date(2026, 10, 1)),
    )
    assert version_id(plan) == f"au:community:sample@2026-10-01#{content_hash(plan)[:8]}"
