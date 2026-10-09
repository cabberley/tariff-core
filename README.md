# tariff-core

`tariff-core` is a pure Python library for modelling electricity, gas and water tariffs. It parses and validates tariff plans, calculates rates and bills, and works offline without Home Assistant or other application-specific dependencies.

## Install

```sh
python -m pip install tariff-core
```

YAML support is optional: install `tariff-core[yaml]` to parse YAML documents.

## Example

This example loads the Origin Ergon fixture, prints its current tariff rate, then bills one day of hourly usage:

```python
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from tariff_core import BillingPeriod, Interval, bill, parse_plan, rate_at

plan = parse_plan(Path("tests/fixtures/plans/origin_ergon.yaml").read_text())
zone = ZoneInfo("Australia/Brisbane")
now = datetime.now(zone)
start = now.replace(hour=0, minute=0, second=0, microsecond=0)
rate = rate_at(plan, now)
print(f"Current rate: {rate.rate} per kWh ({rate.period_label})")
intervals = [
    Interval(start + timedelta(hours=hour), timedelta(hours=1), "general", "kWh", Decimal("1"))
    for hour in range(1, 25)
]
result = bill(
    plan,
    intervals,
    period=BillingPeriod(start, start + timedelta(days=1)),
)
print(f"One-day total: {result.total_rounded}")
```

See the [tariff plan schema](docs/schema.md) for the data model and validation rules.
