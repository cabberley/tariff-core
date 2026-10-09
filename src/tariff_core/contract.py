"""Contract segments, dated conversions and plan overrides."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from tariff_core.models import Commodity, PlanVersion, Register


@dataclass(frozen=True, slots=True)
class Conversion:
    from_: date
    register: Register | str
    meter_unit: str
    billed_unit: str
    heating_value: Decimal
    correction_factor: Decimal


@dataclass(frozen=True, slots=True)
class ContractSegment:
    from_: date | datetime
    to: date | datetime | None
    plan: PlanVersion
    overrides: tuple[Mapping[str, Any], ...] = ()
    catalogue_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Contract:
    commodity: Commodity
    meters: Mapping[str, str] = field(default_factory=dict)
    conversions: tuple[Conversion, ...] = ()
    segments: tuple[ContractSegment, ...] = ()

    def active_segment(self, when: datetime) -> ContractSegment:
        """Return the frozen plan active at an aware instant."""
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("when must be timezone-aware")
        for segment in self.segments:
            timezone = ZoneInfo(segment.plan.timezone) if segment.plan.timezone else when.tzinfo
            start = _boundary(segment.from_, when, timezone)
            end = _boundary(segment.to, when, timezone) if segment.to is not None else None
            instant = when.astimezone(start.tzinfo) if isinstance(segment.from_, datetime) else when
            if start <= instant and (end is None or instant < end):
                return segment
        raise ValueError(f"no contract segment active at {when.isoformat()}")


def _boundary(value: date | datetime, when: datetime, timezone: tzinfo) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("contract segment boundaries must be timezone-aware")
        return value
    return datetime.combine(value, datetime.min.time(), tzinfo=timezone)
