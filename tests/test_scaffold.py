"""Smoke tests for the package skeleton, before tariff logic is implemented."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "tariff_core",
        "tariff_core.models",
        "tariff_core.parse",
        "tariff_core.serialise",
        "tariff_core.validate",
        "tariff_core.schedule",
        "tariff_core.units",
        "tariff_core.contract",
        "tariff_core.engine",
        "tariff_core.engine.rates",
        "tariff_core.engine.bill",
        "tariff_core.engine.demand",
        "tariff_core.engine.blocks",
        "tariff_core.adapters",
        "tariff_core.adapters.cdr",
    ],
)
def test_module_imports(module_name: str) -> None:
    assert import_module(module_name).__name__ == module_name
