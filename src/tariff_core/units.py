"""Exact quantity conversions and contract-specific meter conversions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from tariff_core.contract import Conversion
from tariff_core.models import Register

_FACTORS: dict[str, tuple[str, Decimal]] = {
    "Wh": ("energy", Decimal("0.0036")),
    "kWh": ("energy", Decimal("3.6")),
    "MWh": ("energy", Decimal("3600")),
    "MJ": ("energy", Decimal("1")),
    "GJ": ("energy", Decimal("1000")),
    "therm": ("energy", Decimal("105.5056")),
    "L": ("volume", Decimal("1")),
    "kL": ("volume", Decimal("1000")),
    "m3": ("volume", Decimal("1000")),
    "ft3": ("volume", Decimal("28.316846592")),
    "CCF": ("volume", Decimal("2831.6846592")),
    "gal": ("volume", Decimal("3.785411784")),
    "kW": ("power", Decimal("1")),
    "kVA": ("apparent_power", Decimal("1")),
}


def convert(quantity: Decimal, from_unit: str, to_unit: str) -> Decimal:
    """Convert a quantity without crossing dimensions or losing decimal precision."""
    if not isinstance(quantity, Decimal) or not quantity.is_finite():
        raise TypeError("quantity must be a finite Decimal")
    try:
        from_dimension, from_factor = _FACTORS[from_unit]
        to_dimension, to_factor = _FACTORS[to_unit]
    except KeyError as exc:
        raise ValueError(f"unknown unit {exc.args[0]!r}") from exc
    if from_dimension != to_dimension:
        raise ValueError(f"cannot convert {from_unit} to {to_unit}")
    return quantity * from_factor / to_factor


def to_billed_quantity(
    quantity: Decimal,
    meter_unit: str,
    billed_unit: str,
    *,
    when: datetime,
    register: Register | str = Register.GENERAL,
    conversions: Sequence[Conversion] = (),
) -> Decimal:
    """Convert meter quantities using a dated contract conversion when dimensions differ."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("when must be timezone-aware")
    try:
        return convert(quantity, meter_unit, billed_unit)
    except ValueError as error:
        if meter_unit not in _FACTORS or billed_unit not in _FACTORS:
            raise error
    applicable = [
        conversion
        for conversion in conversions
        if conversion.register == register
        and conversion.meter_unit == meter_unit
        and conversion.billed_unit == billed_unit
        and conversion.from_ <= when.date()
    ]
    if not applicable:
        raise ValueError(f"no contract conversion from {meter_unit} to {billed_unit}")
    conversion = max(applicable, key=lambda item: item.from_)
    if meter_unit == "m3" and billed_unit == "MJ":
        return quantity * conversion.heating_value * conversion.correction_factor
    raise ValueError(f"unsupported contract conversion from {meter_unit} to {billed_unit}")
