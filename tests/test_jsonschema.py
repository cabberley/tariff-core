"""Validate normalized plan fixtures against the generated JSON Schema."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tariff_core import from_cdr, parse_plan, to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "plans"
SCHEMA_PATH = Path(__file__).parents[1] / "src" / "tariff_core" / "jsonschema" / "plan.v1.json"
FIXTURE_PATHS = sorted(
    [
        *FIXTURES.glob("*.json"),
        *FIXTURES.glob("*.yaml"),
        *(FIXTURES / "cdr").glob("*.json"),
        Path(__file__).parents[1] / "src" / "tariff_core" / "data" / "origin_ergon.json",
    ]
)


@pytest.mark.parametrize("fixture", FIXTURE_PATHS)
def test_plan_fixture_validates_against_generated_schema(fixture: Path) -> None:
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    if fixture.parent.name == "cdr":
        plans = from_cdr(
            json.loads(fixture.read_text()),
            retrieved_at=datetime(2026, 10, 9, tzinfo=UTC),
        )
    else:
        plans = [parse_plan(fixture.read_text())]
    for plan in plans:
        Draft202012Validator(schema).validate(to_dict(plan))


def test_component_and_month_day_shapes_are_constrained() -> None:
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    validator = Draft202012Validator(schema)
    assert not validator.is_valid({"commodity": "gas", "components": [{}]})
    assert validator.is_valid({"commodity": "gas", "bundle_id": None})
    assert validator.is_valid(
        {"commodity": "gas", "components": [{"kind": "incentive", "value": None}]}
    )
    assert not validator.is_valid(
        {"commodity": "gas", "seasons": {"winter": {"from": "02-31", "to": "03-01"}}}
    )
    demand = {
        "commodity": "electricity",
        "components": [
            {
                "kind": "demand",
                "method": "max_interval_avg",
                "measure": "kW",
                "unit": "per_kw_per_month",
                "rate": "12",
                "top_n": None,
            }
        ],
    }
    validator.validate(demand)
    parse_plan(demand)

    named_schedule = {
        "commodity": "gas",
        "schedules": {
            "peak": [{"days": "weekdays", "time": ["16:00", "24:00"]}],
        },
        "components": [{"kind": "usage", "schedule": "peak"}],
    }
    validator.validate(named_schedule)
    parse_plan(named_schedule)
    unsupported_frequency = {"commodity": "gas", "billing": {"frequencies": ["P6M"]}}
    assert not validator.is_valid(unsupported_frequency)
    with pytest.raises(ValueError):
        parse_plan(unsupported_frequency)

    source_plan = parse_plan(
        {"commodity": "gas", "source": {"type": "community", "provider": "catalogue-x"}}
    )
    validator.validate(to_dict(source_plan))
