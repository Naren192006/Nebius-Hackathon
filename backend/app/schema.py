"""Decision-record contract v1.1: load, validate, and construct records.

The JSON Schema in ../schema/decision_record.schema.json is the single source of
truth shared with Backend B (verifier) and Backend C (UI). This module includes
a small validator for the subset of JSON Schema used by that file, so the
runtime does not need an extra dependency.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "decision_record.schema.json"
SCHEMA_VERSION = "1.1"

VALID_STATUSES = {"provisional", "verified", "repaired", "unchecked", "failed"}
VALID_VERDICTS = {"supported", "unsupported", "unclear"}

_schema_cache: dict[str, Any] | None = None


class SchemaError(ValueError):
    """Raised when a record does not satisfy the shared schema."""


def load_schema() -> dict[str, Any]:
    global _schema_cache
    if _schema_cache is None:
        with SCHEMA_PATH.open(encoding="utf-8") as handle:
            _schema_cache = json.load(handle)
    return _schema_cache


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"unsupported $ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise SchemaError(f"$ref did not resolve to an object: {ref}")
    return node


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise SchemaError(f"unsupported schema type: {expected}")


def _validate(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> None:
    if "$ref" in schema:
        _validate(value, _resolve_ref(schema["$ref"], root), root, path)
        return
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                _validate(value, option, root, path)
            except SchemaError:
                continue
            matches += 1
        if matches != 1:
            raise SchemaError(f"{path}: expected exactly one matching schema, got {matches}")
        return

    expected_types = schema.get("type")
    if expected_types is not None:
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        if not any(_type_matches(value, expected) for expected in expected_types):
            raise SchemaError(f"{path}: expected {'/'.join(expected_types)}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: {value!r} is not one of {schema['enum']}")

    if value is None:
        return

    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            raise SchemaError(f"{path}: {value} < minimum {schema['minimum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "maximum" in schema:
        if value > schema["maximum"]:
            raise SchemaError(f"{path}: {value} > maximum {schema['maximum']}")

    if isinstance(value, str) and "pattern" in schema:
        if not re.search(schema["pattern"], value):
            raise SchemaError(f"{path}: {value!r} does not match {schema['pattern']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise SchemaError(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], root, f"{path}.{key}")
            elif additional is False:
                raise SchemaError(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, dict):
                _validate(item, additional, root, f"{path}.{key}")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], root, f"{path}[{index}]")


def validate_record(record: dict[str, Any]) -> None:
    """Raise SchemaError if the record breaks the shared contract."""
    _validate(record, load_schema(), load_schema(), "$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_decision_id() -> str:
    return f"dec_{uuid.uuid4().hex[:24]}"


def empty_verification(verifier: str = "stub") -> dict[str, Any]:
    return {
        "verifier": verifier,
        "status": "pending",
        "verification_ms": None,
        "error": None,
    }


def count_verdicts(claims: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"supported": 0, "unsupported": 0, "unclear": 0}
    for claim in claims:
        verdict = claim.get("verdict", "unclear")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def build_record(
    *,
    question: str,
    messages: list[dict[str, str]],
    model_requested: str,
    mode: str,
    routing: dict[str, Any],
    status: str = "provisional",
    answer: dict[str, Any] | None = None,
    claims: list[dict[str, Any]] | None = None,
    repair: dict[str, Any] | None = None,
    verifier: str = "stub",
    verification_status: str = "pending",
    verification_ms: float | None = None,
    verification_error: str | None = None,
    routing_ms: float | None = None,
    total_ms: float = 0.0,
    cost_base: float = 0.0,
    cost_verification: float = 0.0,
    cost_repair: float = 0.0,
    error: str | None = None,
    request_id: str | None = None,
    created_at: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Construct and validate a canonical v1.1 decision record."""
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    created = created_at or utc_now()
    request_id_value = request_id or new_decision_id()

    answer_block: dict[str, Any] | None = None
    if answer is not None:
        answer_block = dict(answer)
        answer_block["status"] = status

    features = dict(routing.get("features") or {})
    route_block = {
        "model": routing["tier"],
        "model_id": routing["model"],
        "confidence": float(routing["confidence"]),
        "reason": str(routing["reason"]),
        "upgraded_for_confidence": bool(features.get("escalated")),
        "features": features,
    }

    repair_block: dict[str, Any] | None = None
    if repair is not None:
        repair_block = {
            "triggered": True,
            "model": str(repair["model"]),
            "previous_text": str(repair["previous_text"]),
        }

    costs = {
        "base": round(float(cost_base), 8),
        "verification": round(float(cost_verification), 8),
        "repair": round(float(cost_repair), 8),
    }
    costs["total"] = round(costs["base"] + costs["verification"] + costs["repair"], 8)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id_value,
        "created_at": created,
        "updated_at": created,
        "mode": mode,
        "status": status,
        "request": {
            "question": question,
            "model_requested": model_requested,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        "route": route_block,
        "answer": answer_block,
        "claims": list(claims or []),
        "repair": repair_block,
        "metrics": {
            "routing_ms": round(float(routing_ms if routing_ms is not None else routing["routing_ms"]), 2),
            "total_ms": round(float(total_ms), 2),
            "cost_usd": costs,
        },
        "verification": {
            "verifier": verifier,
            "status": verification_status,
            "verification_ms": verification_ms,
            "error": verification_error,
        },
        "error": error,
    }
    validate_record(record)
    return record
