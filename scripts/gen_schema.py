"""Generate the published plan JSON Schema from tariff model dataclasses."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, Literal, TypeAliasType, Union, get_args, get_origin, get_type_hints

from tariff_core.models import MonthDay, PlanVersion

SCHEMA_PATH = Path(__file__).parents[1] / "src" / "tariff_core" / "jsonschema" / "plan.v1.json"
FIELD_NAMES = {"from_": "from", "from_register": "from"}


def _schema_for(annotation: Any, definitions: dict[str, Any]) -> dict[str, Any]:
    if annotation is Any:
        return {}
    if annotation is MonthDay:
        return {
            "type": "string",
            "pattern": (
                r"^(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|"
                r"(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|02-(?:0[1-9]|1\d|2[0-9]))$"
            ),
        }
    if isinstance(annotation, TypeAliasType):
        return _schema_for(annotation.__value__, definitions)
    if is_dataclass(annotation):
        name = annotation.__name__
        if name not in definitions:
            definitions[name] = {}
            definitions[name] = _dataclass_schema(annotation, definitions)
        return {"$ref": f"#/$defs/{name}"}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [member.value for member in annotation]}
    if annotation is Decimal:
        return {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    if annotation is datetime:
        return {"type": "string", "format": "date-time"}
    if annotation is date:
        return {"type": "string", "format": "date"}
    if annotation is time:
        return {"type": "string", "pattern": r"^(?:[01]\d|2[0-3]):[0-5]\d$"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is str:
        return {"type": "string"}

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Literal:
        return {"enum": list(arguments)}
    if origin in (Union, UnionType):
        if str in arguments:
            return {"type": "string"}
        variants = [_schema_for(item, definitions) for item in arguments]
        if type(None) in arguments:
            variants = [
                variant
                for item, variant in zip(arguments, variants, strict=True)
                if item is not type(None)
            ]
            return {"anyOf": [*variants, {"type": "null"}]}
        return {"oneOf": variants}
    if origin in (list, tuple, set, frozenset):
        if not arguments:
            return {"type": "array"}
        if origin is frozenset and arguments == (int,):
            return {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                },
                "uniqueItems": True,
            }
        if origin is tuple and len(arguments) > 1 and arguments[-1] is not Ellipsis:
            return {
                "type": "array",
                "prefixItems": [_schema_for(item, definitions) for item in arguments],
                "minItems": len(arguments),
                "maxItems": len(arguments),
            }
        return {
            "type": "array",
            "items": _schema_for(arguments[0], definitions),
        }
    if origin is dict:
        return {
            "type": "object",
            "additionalProperties": _schema_for(arguments[1], definitions),
        }
    return {}


def _dataclass_schema(model: type[Any], definitions: dict[str, Any]) -> dict[str, Any]:
    hints = get_type_hints(model)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in fields(model):
        name = FIELD_NAMES.get(item.name, item.name)
        properties[name] = _schema_for(hints[item.name], definitions)
        if item.default is MISSING and item.default_factory is MISSING:
            required.append(name)
        elif get_origin(hints[item.name]) is Literal:
            required.append(name)
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    if model.__name__ == "PlanVersion":
        result["required"] = ["commodity"]
    return result


def generate_schema() -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    root = _schema_for(PlanVersion, definitions)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/cabberley/tariff-core/plan.v1.json",
        "title": "Tariff Plan v1",
        **root,
        "$defs": definitions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contents = json.dumps(generate_schema(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text() != contents:
            print(
                f"{SCHEMA_PATH} is out of date; run python scripts/gen_schema.py",
                file=sys.stderr,
            )
            return 1
        return 0
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(contents)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
