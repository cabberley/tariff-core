"""Public package API and README usage tests."""

import re
from pathlib import Path

from tariff_core import (
    BillingPeriod,
    Contract,
    Interval,
    bill,
    from_cdr,
    parse_contract,
    parse_plan,
    rate_at,
    validate_contract,
    validate_plan,
)

ROOT = Path(__file__).parents[1]


def test_public_imports_are_available_from_package_root() -> None:
    assert all(
        callable(function)
        for function in (
            parse_plan,
            parse_contract,
            validate_plan,
            validate_contract,
            bill,
            rate_at,
            from_cdr,
        )
    )
    assert Contract is not None
    assert BillingPeriod is not None
    assert Interval is not None


def test_contract_parser_and_validator_are_public() -> None:
    contract = parse_contract(
        {
            "commodity": "gas",
            "segments": [
                {
                    "from": "2026-01-01",
                    "plan": {"commodity": "gas"},
                }
            ],
        }
    )
    assert contract.commodity.value == "gas"
    assert validate_contract(contract) == []


def test_readme_example_runs() -> None:
    readme = (ROOT / "README.md").read_text()
    example = re.search(r"```python\n(.*?)\n```", readme, flags=re.DOTALL)
    assert example is not None
    namespace = {"__file__": str(ROOT / "README.md")}
    exec(compile(example.group(1), "README.md", "exec"), namespace)
    assert namespace["result"].total_rounded >= 0
