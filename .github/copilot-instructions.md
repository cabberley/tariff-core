# Copilot instructions: tariff-core

## What this repo is

`tariff-core` is a pure Python library that models electricity, gas and water tariff plans, validates them and calculates bills and rates from interval usage data. It is the single source of truth for the plan schema (`docs/schema.md`).

It is consumed by:

- `tariff-catalogue`: harvests and publishes plans; uses this library to normalise and validate them
- `ha-energy-tariff`: the Home Assistant custom integration; uses this library to evaluate plans locally
- potentially other tools (evcc, EMHASS, Predbat), so the API must not assume Home Assistant

Read `docs/schema.md` before changing any model, validator or engine code.

## Hard constraints

- **No I/O.** No network calls, no file system access outside explicit `load_*`/`dump_*` helpers that take a path or a string. No logging configuration. The library must be importable and fully usable offline.
- **No Home Assistant imports**, ever. Home Assistant-specific concepts (entities, `hass`, config entries) do not exist here.
- **Synchronous, side-effect-free functions.** Home Assistant will call this from an executor or wrap it; do not add `async`.
- **Money is `decimal.Decimal`.** Never use `float` for rates, costs or credits. Parse JSON numbers and strings into `Decimal` at the boundary; reject floats in monetary fields.
- **Time uses `zoneinfo` and timezone-aware `datetime` only.** Naive datetimes are a `ValueError`.
- **Minimal dependencies.** Home Assistant pins many packages, so conflicts are costly. Allowed runtime dependencies: `python-holidays` (optional extra `[holidays]`). Do not add `pydantic`, `pandas`, `numpy`, `attrs` or `arrow`. Use `dataclasses` (`frozen=True`, `slots=True`) and the standard library.
- **Python version:** match the oldest Python supported by current Home Assistant releases (3.13 at time of writing). Check `pyproject.toml` `requires-python`.

## Package layout

```
src/tariff_core/
  __init__.py          # public API re-exports only
  models.py            # frozen dataclasses mirroring docs/schema.md
  parse.py             # dict/JSON/YAML -> models; strict, with useful error paths
  serialise.py         # models -> canonical JSON; content hashing
  validate.py          # structural + semantic validation (coverage, refs, codes)
  schedule.py          # window matching, half-open intervals, midnight wrap, seasons, holidays
  units.py             # dimensions, exact Decimal conversion constants, contract conversions
  engine/
    rates.py           # rate_at(), forecast()
    bill.py            # bill() -> Bill with line items
    demand.py          # demand charge methods
    blocks.py          # stepped/block pricing and pro-rating
  contract.py          # Contract, ContractSegment, segment lookup, overrides (JSON pointer)
  adapters/            # source-format -> PlanVersion converters (pure functions)
    cdr.py             # Australian CDR Energy plan detail (x-v 3), electricity and gas -> PlanVersion
  jsonschema/
    plan.v1.json       # generated, do not hand-edit (see scripts/gen_schema.py)
tests/
docs/schema.md
```

Adapters live here (not in `tariff-catalogue`) because they are pure transformations and both the pipeline and tests need them. Fetching data is **not** an adapter's job.

## Public API (keep stable; changes need a changelog entry)

```python
parse_plan(data: Mapping | str) -> PlanVersion
validate_plan(plan: PlanVersion) -> list[ValidationIssue]   # empty = valid
validate_contract(contract: Contract) -> list[ValidationIssue]
convert(quantity: Decimal, from_unit: str, to_unit: str) -> Decimal   # same dimension only
content_hash(plan: PlanVersion) -> str
dump_plan(plan: PlanVersion) -> str                         # canonical JSON

rate_at(plan, when: datetime, *, register="general", direction="import",
        resolved: Mapping[str, Decimal] | None = None) -> RateInfo
forecast(plan, start: datetime, end: datetime, *, register="general",
         direction="import") -> list[RateSlot]

bill(plan, intervals: Iterable[Interval], *, period: BillingPeriod,
     resolved_rates: RateResolver | None = None) -> Bill

Contract.active_segment(when: datetime) -> ContractSegment
```

- `RateInfo` carries: `rate`, `period`, `period_label`, `next_change`, `next_rate`, `next_period`.
- `Interval` carries: `end` (interval-ending timestamp), `duration`, `register`, `unit`, `import_qty`, `export_qty`, optional `demand_kw`/`demand_kva`. Quantities are in the meter's unit; `bill()` converts to each component's `quantity_unit` using `units.py` and the contract's `conversions`. Intervals may be long (daily, monthly, quarterly reads for gas and water).
- `Bill` carries line items (`component`, `quantity`, `unit`, `rate`, `amount`, `type: cost | credit`), subtotals, tax, total and a list of warnings (e.g. dynamic rate missing for 3 intervals).
- Dynamic rates (`rate: {entity: …}`) are resolved by the caller through `RateResolver`. The library never reads entities.

## Behaviour rules (from the schema; tests must enforce them)

- Windows are half-open `[start, end)`. Midnight-wrapping windows are normal.
- Interval data is interval-ending. Window lookup uses the interval **start** (`end - duration`).
- CDR inclusive end times (`HH:59`) are converted to the next minute in `adapters/cdr.py`.
- Export credits are negative rates internally; `Bill` line items always show positive amounts with `type`.
- Units convert within a dimension only (energy, volume, power). Gas volume → energy uses the contract's dated `conversions` (heating value × correction factor); never a built-in constant. `gal` is the US gallon.
- Derived quantities (e.g. sewerage = 0.85 × water) are computed from their source register before billing; cycles are a validation error.
- Long intervals that span a season or rate change are split pro-rata by elapsed time, with a warning on the `Bill`.
- Every `(register, direction, season)` with usage components must cover 168 h/week after precedence (scheduled beats anytime, seasonal beats all-year); schedules are lists of windows; seasons are MM-DD half-open ranges covering the year.
- Demand: average power over each interval (`kWh / hours`), not instantaneous, unless `method: max_instant`.
- Block pricing: thresholds pro-rated by billing-period length when `prorate: true`.
- Conditional discounts are excluded from `bill()` unless `include_conditional=True`.
- Incentives never affect `bill()`.

## Testing

- `pytest`, `pytest-cov`; target ≥ 95% line coverage on `engine/` and `schedule.py`.
- Use `hypothesis` for schedule coverage and window matching (random windows, midnight wraps, DST transitions in `Australia/Sydney` and `Europe/Amsterdam`).
- Golden tests in `tests/golden/`: real plans (e.g. the Origin Ergon plan `ORI1161031MRE3@EME`, Energex `6900`, one Origin Queensland gas plan, and one hand-built water plan with derived sewerage) with hand-calculated bills from fixed interval CSVs. A golden test must document where its expected numbers came from.
- Every bug fix starts with a failing test.
- Never use floats in test expectations for money; compare `Decimal`s.

## Tooling

- `ruff` (lint + format), `mypy --strict` on `src/`.
- `pyproject.toml` with `hatchling`; version from git tags.
- CI: lint, type-check, tests on all supported Python versions, regenerate JSON Schema and fail if it differs from the committed file.

## Style

- Small pure functions, explicit types, no global state, no singletons.
- Error messages include the JSON path of the problem (e.g. `components[3].schedule`).
- Docstrings explain *why* and units; do not restate the signature.
- Prefer clear names from the schema vocabulary (`commodity`, `register`, `direction`, `period`, `quantity_unit`) over synonyms. Never name anything `kwh` unless it is specifically kWh.

## Do not

- Add fetching, caching or scheduling code.
- Introduce float arithmetic for money "for performance".
- Add Home Assistant, evcc or vendor-specific concepts to models.
- Change `docs/schema.md` semantics without bumping the schema version and adding a migration.
