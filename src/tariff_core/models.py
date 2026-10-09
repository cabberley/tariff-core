"""Immutable tariff models mirroring docs/schema.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal


class Commodity(StrEnum):
    ELECTRICITY = "electricity"
    GAS = "gas"
    WATER = "water"


class CustomerType(StrEnum):
    RESIDENTIAL = "residential"
    BUSINESS = "business"


class PlanKind(StrEnum):
    RETAIL = "retail"
    NETWORK = "network"


class PricingModel(StrEnum):
    PASS_THROUGH = "pass_through"
    BUNDLED = "bundled"


class Direction(StrEnum):
    IMPORT = "import"
    EXPORT = "export"


class Register(StrEnum):
    GENERAL = "general"
    CONTROLLED_LOAD_1 = "controlled_load_1"
    CONTROLLED_LOAD_2 = "controlled_load_2"
    RECYCLED = "recycled"
    SEWERAGE = "sewerage"


class Period(StrEnum):
    PEAK = "peak"
    SHOULDER = "shoulder"
    OFF_PEAK = "off_peak"
    SOLAR_SOAK = "solar_soak"
    CRITICAL_PEAK = "critical_peak"
    SINGLE = "single"


class FixedLabel(StrEnum):
    SUPPLY = "supply"
    METERING = "metering"
    SUBSCRIPTION = "subscription"
    WATER_ACCESS = "water_access"
    SEWERAGE_ACCESS = "sewerage_access"
    OTHER = "other"


class FixedUnit(StrEnum):
    PER_DAY = "per_day"
    PER_MONTH = "per_month"
    PER_QUARTER = "per_quarter"
    PER_YEAR = "per_year"


class DemandMethod(StrEnum):
    MAX_INTERVAL_AVG = "max_interval_avg"
    MAX_INSTANT = "max_instant"
    TOP_N_AVG = "top_n_avg"
    ROLLING_12M_MAX = "rolling_12m_max"


class DemandMeasure(StrEnum):
    KW = "kW"
    KVA = "kVA"


class DemandUnit(StrEnum):
    PER_KW_PER_MONTH = "per_kw_per_month"
    PER_KW_PER_DAY = "per_kw_per_day"
    PER_KVA_PER_MONTH = "per_kva_per_month"


class DemandReset(StrEnum):
    MONTHLY = "monthly"
    BILLING_PERIOD = "billing_period"


class BlockPeriod(StrEnum):
    DAY = "day"
    BILLING_PERIOD = "billing_period"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class SourceType(StrEnum):
    OFFICIAL_FEED = "official_feed"
    COMMUNITY = "community"
    FORMULA = "formula"
    USER = "user"
    NETWORK_LIST = "network_list"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNVERIFIED = "unverified"


class TimeBasis(StrEnum):
    LOCAL = "local"
    MARKET_STANDARD = "market_standard"


class HolidayTreatment(StrEnum):
    NONE = "none"
    AS_WEEKEND = "as_weekend"
    OWN_SCHEDULE = "own_schedule"


class BillingFrequency(StrEnum):
    P1M = "P1M"
    P3M = "P3M"


@dataclass(frozen=True, slots=True, order=True)
class MonthDay:
    """A recurring month/day boundary that can represent February 29."""

    month: int
    day: int

    def __post_init__(self) -> None:
        date(2000, self.month, self.day)


@dataclass(frozen=True, slots=True)
class Supplier:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Region:
    country: str
    network: str | None = None
    zone: str | None = None


@dataclass(frozen=True, slots=True)
class Tax:
    inclusive: bool
    rate: Decimal


@dataclass(frozen=True, slots=True)
class Holidays:
    calendar: str | None = None
    treatment: HolidayTreatment = HolidayTreatment.NONE


@dataclass(frozen=True, slots=True)
class Effective:
    from_: date | None = None
    to: date | None = None


@dataclass(frozen=True, slots=True)
class Billing:
    frequencies: tuple[BillingFrequency, ...] = ()


@dataclass(frozen=True, slots=True)
class Season:
    from_: MonthDay
    to: MonthDay


@dataclass(frozen=True, slots=True)
class Window:
    days: frozenset[int]
    time: tuple[time, time]


type Schedule = tuple[Window, ...]


@dataclass(frozen=True, slots=True)
class Source:
    type: SourceType | None = None
    feed: str | None = None
    url: str | None = None
    retrieved_at: datetime | None = None
    raw_sha256: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BlockTier:
    rate: Decimal
    up_to: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Blocks:
    period: BlockPeriod
    prorate: bool
    tiers: tuple[BlockTier, ...]


@dataclass(frozen=True, slots=True)
class DerivedQuantity:
    from_register: Register
    factor: Decimal


@dataclass(frozen=True, slots=True)
class RateSource:
    entity: str
    multiplier: Decimal = Decimal("1")
    adder: Decimal = Decimal("0")
    unit_scale: Decimal = Decimal("1")


@dataclass(frozen=True, slots=True)
class FixedComponent:
    label: FixedLabel
    unit: FixedUnit
    rate: Decimal
    register: Register | None = None
    season: str | None = None
    kind: Literal["fixed"] = "fixed"


@dataclass(frozen=True, slots=True)
class UsageComponent:
    direction: Direction = Direction.IMPORT
    register: Register = Register.GENERAL
    quantity_unit: str = "kWh"
    schedule: Schedule | None = None
    season: str | None = None
    period: Period | None = None
    period_label: str | None = None
    rate: Decimal | RateSource | None = None
    blocks: Blocks | None = None
    quantity: DerivedQuantity | None = None
    label: str | None = None
    stack: bool = False
    kind: Literal["usage"] = "usage"


@dataclass(frozen=True, slots=True)
class DemandComponent:
    method: DemandMethod
    measure: DemandMeasure
    unit: DemandUnit
    rate: Decimal
    schedule: Schedule | None = None
    season: str | None = None
    interval_minutes: int | None = None
    top_n: int | None = None
    threshold: Decimal | None = None
    reset: DemandReset = DemandReset.MONTHLY
    kind: Literal["demand"] = "demand"


@dataclass(frozen=True, slots=True)
class DiscountComponent:
    applies_to: tuple[str, ...]
    percent: Decimal
    conditional: str | None = None
    schedule: Schedule | None = None
    season: str | None = None
    kind: Literal["discount"] = "discount"


@dataclass(frozen=True, slots=True)
class IncentiveComponent:
    label: str | None = None
    description: str | None = None
    value: Decimal | str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    kind: Literal["incentive"] = "incentive"


type Component = (
    FixedComponent | UsageComponent | DemandComponent | DiscountComponent | IncentiveComponent
)


@dataclass(frozen=True, slots=True)
class PlanVersion:
    commodity: Commodity
    id: str | None = None
    plan_id: str | None = None
    kind: PlanKind = PlanKind.RETAIL
    display_name: str | None = None
    supplier: Supplier | None = None
    bundle_id: str | None = None
    customer_type: CustomerType = CustomerType.RESIDENTIAL
    region: Region | None = None
    currency: str | None = None
    tax: Tax | None = None
    timezone: str | None = None
    time_basis: TimeBasis = TimeBasis.LOCAL
    holidays: Holidays | None = None
    effective: Effective | None = None
    pricing_model: PricingModel = PricingModel.BUNDLED
    network_tariff_ref: str | None = None
    billing: Billing = field(default_factory=Billing)
    seasons: dict[str, Season] = field(default_factory=dict)
    schedules: dict[str, Schedule] = field(default_factory=dict)
    components: tuple[Component, ...] = ()
    source: Source | None = None
    confidence: Confidence | None = None
    partial: bool = False
    unmapped_features: tuple[Any, ...] = ()
    notes: tuple[Any, ...] = ()
