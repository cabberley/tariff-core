"""Stepped pricing and billing-period threshold pro-rating."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import NamedTuple

from tariff_core.models import Blocks


class TierQuantity(NamedTuple):
    tier_index: int
    quantity: Decimal
    rate: Decimal


def allocate_blocks(
    blocks: Blocks,
    quantity: Decimal,
    *,
    used: Decimal = Decimal(0),
    period_days: Decimal = Decimal(1),
) -> tuple[TierQuantity, ...]:
    """Allocate quantity across tiers from the previously consumed threshold position."""
    if not blocks.tiers:
        raise ValueError("block pricing requires at least one tier")
    if quantity < 0 or used < 0:
        raise ValueError("block quantities cannot be negative")
    if not quantity.is_finite() or not used.is_finite() or not period_days.is_finite():
        raise ValueError("block quantities and period length must be finite")
    tiers: list[TierQuantity] = []
    position = used
    remaining = quantity
    for index, tier in enumerate(blocks.tiers):
        if remaining <= 0:
            break
        limit = tier.up_to
        if limit is None:
            available = remaining
        else:
            if blocks.prorate and blocks.period.value in {"month", "quarter", "year"}:
                nominal = {
                    "month": Decimal(365) / Decimal(12),
                    "quarter": Decimal(365) / Decimal(4),
                    "year": Decimal(365),
                }[blocks.period.value]
                limit *= period_days / nominal
            available = max(Decimal(0), limit - position)
        charged = min(remaining, available)
        if charged:
            tiers.append(TierQuantity(index, charged, tier.rate))
            remaining -= charged
            position += charged
        if limit is not None and position >= limit:
            continue
    if remaining > 0:
        if blocks.tiers[-1].up_to is not None:
            raise ValueError("block tiers must end with an unlimited tier")
        last = blocks.tiers[-1]
        if tiers and tiers[-1].tier_index == len(blocks.tiers) - 1:
            previous = tiers[-1]
            tiers[-1] = TierQuantity(
                previous.tier_index, previous.quantity + remaining, previous.rate
            )
        else:
            tiers.append(TierQuantity(len(blocks.tiers) - 1, remaining, last.rate))
    return tuple(tiers)


def block_period_key(period: str, local_date: date, billing_start: date) -> tuple[int, ...]:
    """Return the local reset key for a block accumulator."""
    if period == "day":
        return (local_date.toordinal(),)
    if period == "month":
        return (local_date.year, local_date.month)
    if period == "quarter":
        return (local_date.year, (local_date.month - 1) // 3)
    if period == "year":
        return (local_date.year,)
    return (billing_start.toordinal(),)
