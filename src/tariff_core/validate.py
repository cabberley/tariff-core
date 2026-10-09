"""Structural and semantic validation of plans and contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tariff_core.contract import Contract
from tariff_core.models import (
    DemandComponent,
    DiscountComponent,
    FixedComponent,
    PlanVersion,
    RateSource,
    UsageComponent,
)
from tariff_core.units import convert


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str


def _valid_decimal(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def validate_plan(plan: PlanVersion) -> list[ValidationIssue]:
    """Report basic structural and semantic plan problems without performing I/O."""
    if not isinstance(plan, PlanVersion):
        return [ValidationIssue("$", "expected a PlanVersion")]
    issues: list[ValidationIssue] = []
    if plan.currency is not None and (
        len(plan.currency) != 3 or not plan.currency.isascii() or not plan.currency.isalpha()
    ):
        issues.append(ValidationIssue("$.currency", "expected a three-letter currency code"))
    if plan.timezone is not None:
        try:
            ZoneInfo(plan.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            issues.append(ValidationIssue("$.timezone", "expected a valid IANA timezone"))
    if plan.pricing_model.value == "pass_through" and not plan.network_tariff_ref:
        issues.append(ValidationIssue("$.network_tariff_ref", "required for pass-through pricing"))
    if plan.network_tariff_ref and plan.commodity.value != "electricity":
        issues.append(
            ValidationIssue(
                "$.network_tariff_ref",
                "only electricity plans may reference a network tariff",
            )
        )
    if plan.tax is not None and not _valid_decimal(plan.tax.rate):
        issues.append(ValidationIssue("$.tax.rate", "expected a finite Decimal"))

    for index, component in enumerate(plan.components):
        path = f"$.components[{index}]"
        if isinstance(component, FixedComponent) and not _valid_decimal(component.rate):
            issues.append(ValidationIssue(f"{path}.rate", "expected a finite Decimal"))
        elif isinstance(component, UsageComponent):
            if isinstance(component.rate, Decimal) and not _valid_decimal(component.rate):
                issues.append(ValidationIssue(f"{path}.rate", "expected a finite Decimal"))
            if isinstance(component.rate, RateSource) and not all(
                _valid_decimal(value)
                for value in (
                    component.rate.multiplier,
                    component.rate.adder,
                    component.rate.unit_scale,
                )
            ):
                issues.append(ValidationIssue(f"{path}.rate", "expected finite Decimal values"))
            if component.blocks is not None:
                for tier_index, tier in enumerate(component.blocks.tiers):
                    tier_path = f"{path}.blocks.tiers[{tier_index}]"
                    if not _valid_decimal(tier.rate):
                        issues.append(
                            ValidationIssue(f"{tier_path}.rate", "expected a finite Decimal")
                        )
                    if tier.up_to is not None and not _valid_decimal(tier.up_to):
                        issues.append(
                            ValidationIssue(f"{tier_path}.up_to", "expected a finite Decimal")
                        )
            if component.direction.value == "export" and plan.commodity.value != "electricity":
                issues.append(
                    ValidationIssue(f"{path}.direction", "export usage requires electricity")
                )
        elif isinstance(component, DemandComponent):
            if plan.commodity.value != "electricity":
                issues.append(ValidationIssue(path, "demand charges require electricity"))
            if not _valid_decimal(component.rate):
                issues.append(ValidationIssue(f"{path}.rate", "expected a finite Decimal"))
        elif isinstance(component, DiscountComponent) and not _valid_decimal(component.percent):
            issues.append(ValidationIssue(f"{path}.percent", "expected a finite Decimal"))
    return issues


def validate_contract(contract: Contract) -> list[ValidationIssue]:
    """Check plan/contract commodity consistency and meter-to-billed unit compatibility."""
    if not isinstance(contract, Contract):
        return [ValidationIssue("$", "expected a Contract")]
    issues: list[ValidationIssue] = []
    for index, segment in enumerate(contract.segments):
        path = f"$.segments[{index}]"
        if segment.plan.commodity is not contract.commodity:
            issues.append(
                ValidationIssue(f"{path}.plan.commodity", "must match the contract commodity")
            )
        issues.extend(
            ValidationIssue(f"{path}.plan{issue.path[1:]}", issue.message)
            for issue in validate_plan(segment.plan)
        )
        segment_start = (
            segment.from_.date() if isinstance(segment.from_, datetime) else segment.from_
        )
        for component_index, component in enumerate(segment.plan.components):
            if not isinstance(component, UsageComponent):
                continue
            register = str(component.register)
            meter_unit = contract.meters.get(register)
            if meter_unit is None:
                continue
            try:
                convert(Decimal(1), meter_unit, component.quantity_unit)
            except ValueError:
                has_conversion = any(
                    conversion.register == component.register
                    and conversion.meter_unit == meter_unit
                    and conversion.billed_unit == component.quantity_unit
                    and conversion.from_ <= segment_start
                    for conversion in contract.conversions
                )
                if not has_conversion:
                    issues.append(
                        ValidationIssue(
                            f"{path}.plan.components[{component_index}].quantity_unit",
                            f"no conversion from {meter_unit} to {component.quantity_unit} "
                            "covers the segment start",
                        )
                    )
    return issues
