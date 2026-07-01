#!/usr/bin/env python3
"""Validate prefab hierarchy JSON against the repo JSON Schema.

The schema source of truth is:
  .codex/agents/examples/prefab_hierarchy_schema.json

This script intentionally has no third-party dependency. It implements the
small JSON Schema Draft-07 subset used by the prefab hierarchy schema.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SCHEMA_PATH = Path(".codex/agents/examples/prefab_hierarchy_schema.json")


@dataclass
class Issue:
    path: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def display_path(path: str) -> str:
    return path or "$"


def resolve_ref(ref: str, root_schema: dict[str, Any]) -> dict[str, Any]:
    if ref == "#":
        return root_schema
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref: {ref}")
    current: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    if not isinstance(current, dict):
        raise ValueError(f"$ref does not resolve to a schema object: {ref}")
    return current


def validate_instance(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
) -> list[Issue]:
    issues: list[Issue] = []

    if "$ref" in schema:
        return validate_instance(instance, resolve_ref(schema["$ref"], root_schema), root_schema, path)

    schema_type = schema.get("type")
    if isinstance(schema_type, str) and not json_type_matches(instance, schema_type):
        return [Issue(display_path(path), f"expected {schema_type}, got {type_name(instance)}")]
    if isinstance(schema_type, list) and not any(json_type_matches(instance, t) for t in schema_type):
        return [Issue(display_path(path), f"expected one of {schema_type}, got {type_name(instance)}")]

    if "enum" in schema and instance not in schema["enum"]:
        issues.append(Issue(display_path(path), f"value {instance!r} is not one of {schema['enum']}"))

    if "const" in schema and instance != schema["const"]:
        issues.append(Issue(display_path(path), f"value {instance!r} does not equal const {schema['const']!r}"))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if minimum is not None and instance < minimum:
            issues.append(Issue(display_path(path), f"value {instance!r} is less than minimum {minimum!r}"))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for field in required:
            if field not in instance:
                issues.append(Issue(display_path(path), f"missing required field '{field}'"))

        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for field, property_schema in properties.items():
                if field in instance:
                    issues.extend(
                        validate_instance(
                            instance[field],
                            property_schema,
                            root_schema,
                            f"{path}.{field}" if path else field,
                        )
                    )

        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            allowed = set(properties)
            for field in sorted(set(instance) - allowed):
                issues.append(Issue(display_path(f"{path}.{field}" if path else field), "additional property is not allowed"))

    if isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                issues.extend(validate_instance(item, item_schema, root_schema, f"{path}[{index}]"))

    for sub_schema in schema.get("allOf", []):
        if isinstance(sub_schema, dict):
            issues.extend(validate_conditional_or_schema(instance, sub_schema, root_schema, path))

    return issues


def validate_conditional_or_schema(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
) -> list[Issue]:
    if "if" in schema and "then" in schema:
        condition_issues = validate_instance(instance, schema["if"], root_schema, path)
        if not condition_issues:
            return validate_instance(instance, schema["then"], root_schema, path)
        return []
    return validate_instance(instance, schema, root_schema, path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate prefab hierarchy JSON against prefab_hierarchy_schema.json.")
    parser.add_argument("hierarchy_json", help="Path to prefab hierarchy JSON")
    parser.add_argument(
        "--schema",
        default=str(DEFAULT_SCHEMA_PATH),
        help=f"Path to JSON Schema file (default: {DEFAULT_SCHEMA_PATH})",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON report")
    args = parser.parse_args()

    hierarchy_path = Path(args.hierarchy_json)
    schema_path = Path(args.schema)

    try:
        hierarchy = load_json(hierarchy_path)
        schema = load_json(schema_path)
    except FileNotFoundError as exc:
        report = {"ok": False, "errors": [{"path": str(exc.filename), "message": "file not found"}]}
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2
    except json.JSONDecodeError as exc:
        report = {"ok": False, "errors": [{"path": str(hierarchy_path), "message": f"invalid JSON: {exc}"}]}
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    issues = validate_instance(hierarchy, schema, schema, "$")
    report = {
        "ok": not issues,
        "schema_path": str(schema_path),
        "hierarchy_path": str(hierarchy_path),
        "summary": {"errors": len(issues)},
        "errors": [issue.to_json() for issue in issues],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
