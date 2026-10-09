"""Canonical plan serialisation and content hashing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

from tariff_core.models import (
    DemandComponent,
    DiscountComponent,
    FixedComponent,
    IncentiveComponent,
    PlanVersion,
    UsageComponent,
    Window,
)

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal values must be finite")
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return "0" if normalized in {"-0", ""} else normalized


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _window_dict(window: Window) -> dict[str, Any]:
    return {
        "days": [_DAYS[day] for day in sorted(window.days)],
        "time": [window.time[0].strftime("%H:%M"), window.time[1].strftime("%H:%M")],
    }


def _component_dict(component: Any) -> dict[str, Any]:
    if isinstance(component, FixedComponent):
        result: dict[str, Any] = {
            "kind": component.kind,
            "label": component.label.value,
            "unit": component.unit.value,
            "rate": _decimal(component.rate),
        }
        if component.register is not None:
            result["register"] = component.register.value
        if component.season is not None:
            result["season"] = component.season
        return result

    if isinstance(component, UsageComponent):
        result = {
            "kind": component.kind,
            "direction": component.direction.value,
            "register": component.register.value,
            "quantity_unit": component.quantity_unit,
        }
        if component.schedule is not None:
            result["schedule"] = [_window_dict(window) for window in component.schedule]
        if component.season is not None:
            result["season"] = component.season
        if component.period is not None:
            result["period"] = component.period.value
        if component.period_label is not None:
            result["period_label"] = component.period_label
        if isinstance(component.rate, Decimal):
            result["rate"] = _decimal(component.rate)
        elif component.rate is not None:
            result["rate"] = {
                "entity": component.rate.entity,
                "multiplier": _decimal(component.rate.multiplier),
                "adder": _decimal(component.rate.adder),
                "unit_scale": _decimal(component.rate.unit_scale),
            }
        if component.blocks is not None:
            result["blocks"] = {
                "period": component.blocks.period.value,
                "prorate": component.blocks.prorate,
                "tiers": [
                    {
                        **({"up_to": _decimal(tier.up_to)} if tier.up_to is not None else {}),
                        "rate": _decimal(tier.rate),
                    }
                    for tier in component.blocks.tiers
                ],
            }
        if component.quantity is not None:
            result["quantity"] = {
                "from": component.quantity.from_register.value,
                "factor": _decimal(component.quantity.factor),
            }
        if component.label is not None:
            result["label"] = component.label
        return result

    if isinstance(component, DemandComponent):
        result = {
            "kind": component.kind,
            "method": component.method.value,
            "measure": component.measure.value,
            "unit": component.unit.value,
            "rate": _decimal(component.rate),
            "reset": component.reset.value,
        }
        if component.schedule is not None:
            result["schedule"] = [_window_dict(window) for window in component.schedule]
        if component.season is not None:
            result["season"] = component.season
        if component.interval_minutes is not None:
            result["interval_minutes"] = component.interval_minutes
        if component.top_n is not None:
            result["top_n"] = component.top_n
        if component.threshold is not None:
            result["threshold"] = _decimal(component.threshold)
        return result

    if isinstance(component, DiscountComponent):
        result = {
            "kind": component.kind,
            "applies_to": list(component.applies_to),
            "percent": _decimal(component.percent),
        }
        if component.conditional is not None:
            result["conditional"] = component.conditional
        if component.schedule is not None:
            result["schedule"] = [_window_dict(window) for window in component.schedule]
        if component.season is not None:
            result["season"] = component.season
        return result

    if isinstance(component, IncentiveComponent):
        result = {"kind": component.kind}
        if component.label is not None:
            result["label"] = component.label
        if component.description is not None:
            result["description"] = component.description
        if component.value is not None:
            result["value"] = (
                _decimal(component.value)
                if isinstance(component.value, Decimal)
                else component.value
            )
        if component.details:
            result["details"] = _json_value(component.details)
        return result

    raise TypeError(f"unsupported component type: {type(component).__name__}")


def to_dict(plan: PlanVersion) -> dict[str, Any]:
    """Convert a plan to the schema's JSON-compatible mapping."""
    result: dict[str, Any] = {
        "commodity": plan.commodity.value,
        "kind": plan.kind.value,
        "customer_type": plan.customer_type.value,
        "time_basis": plan.time_basis.value,
        "pricing_model": plan.pricing_model.value,
        "billing": {
            "frequencies": [_json_value(frequency) for frequency in plan.billing.frequencies]
        },
        "seasons": {
            name: {
                "from": f"{season.from_.month:02d}-{season.from_.day:02d}",
                "to": f"{season.to.month:02d}-{season.to.day:02d}",
            }
            for name, season in plan.seasons.items()
        },
        "schedules": {
            name: [_window_dict(window) for window in schedule]
            for name, schedule in plan.schedules.items()
        },
        "components": [_component_dict(component) for component in plan.components],
        "partial": plan.partial,
        "unmapped_features": _json_value(plan.unmapped_features),
        "notes": _json_value(plan.notes),
    }
    optional_values = {
        "id": plan.id,
        "plan_id": plan.plan_id,
        "display_name": plan.display_name,
        "bundle_id": plan.bundle_id,
        "currency": plan.currency,
        "timezone": plan.timezone,
        "network_tariff_ref": plan.network_tariff_ref,
    }
    result.update({key: value for key, value in optional_values.items() if value is not None})
    if plan.supplier is not None:
        result["supplier"] = {"id": plan.supplier.id, "name": plan.supplier.name}
    if plan.region is not None:
        result["region"] = {
            key: value
            for key, value in {
                "country": plan.region.country,
                "network": plan.region.network,
                "zone": plan.region.zone,
            }.items()
            if value is not None
        }
    if plan.tax is not None:
        result["tax"] = {"inclusive": plan.tax.inclusive, "rate": _decimal(plan.tax.rate)}
    if plan.holidays is not None:
        holidays: dict[str, Any] = {"treatment": plan.holidays.treatment.value}
        if plan.holidays.calendar is not None:
            holidays["calendar"] = plan.holidays.calendar
        result["holidays"] = holidays
    if plan.effective is not None:
        result["effective"] = {
            "from": plan.effective.from_.isoformat() if plan.effective.from_ is not None else None,
            "to": plan.effective.to.isoformat() if plan.effective.to is not None else None,
        }
    if plan.source is not None:
        source: dict[str, Any] = _json_value(plan.source.extra)
        source.update(
            {
                key: value
                for key, value in {
                    "type": plan.source.type.value if plan.source.type is not None else None,
                    "feed": plan.source.feed,
                    "url": plan.source.url,
                    "retrieved_at": (
                        plan.source.retrieved_at.isoformat()
                        if plan.source.retrieved_at is not None
                        else None
                    ),
                    "raw_sha256": plan.source.raw_sha256,
                }.items()
                if value is not None
            }
        )
        result["source"] = source
    if plan.confidence is not None:
        result["confidence"] = plan.confidence.value
    return result


def dump_plan(plan: PlanVersion) -> str:
    """Return stable, compact UTF-8-compatible JSON for a plan."""
    return json.dumps(to_dict(plan), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(plan: PlanVersion) -> str:
    """Hash canonical plan content while excluding identifiers and source metadata."""
    content = to_dict(plan)
    for field in ("id", "source", "confidence"):
        content.pop(field, None)
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def version_id(plan: PlanVersion) -> str:
    """Build the stable version identifier from the plan ID, effective date, and content."""
    if plan.plan_id is None:
        raise ValueError("plan_id is required to build a version ID")
    if plan.effective is None or plan.effective.from_ is None:
        raise ValueError("effective.from is required to build a version ID")
    return f"{plan.plan_id}@{plan.effective.from_.isoformat()}#{content_hash(plan)[:8]}"
