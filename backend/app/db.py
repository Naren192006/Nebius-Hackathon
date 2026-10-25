"""SQLite persistence for decision records.

One table, one JSON column. The record shape itself lives in the shared schema;
this module never reshapes it, so /decisions consumers see exactly what was
validated at write time.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from . import config, schema

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions (status);
"""


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(_SCHEMA_SQL)


def _dump(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def insert_decision(record: dict[str, Any]) -> None:
    schema.validate_record(record)
    with connection() as conn:
        conn.execute(
            "INSERT INTO decisions (id, created_at, status, record_json) VALUES (?, ?, ?, ?)",
            (record["request_id"], record["created_at"], record["status"], _dump(record)),
        )


def update_decision(record: dict[str, Any]) -> None:
    record["updated_at"] = schema.utc_now()
    schema.validate_record(record)
    with connection() as conn:
        cursor = conn.execute(
            "UPDATE decisions SET status = ?, record_json = ? WHERE id = ?",
            (record["status"], _dump(record), record["request_id"]),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"decision {record['request_id']} not found")


def get_decision(decision_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT record_json FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["record_json"])


def list_decisions(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    with connection() as conn:
        rows = conn.execute(
            "SELECT record_json FROM decisions ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_summary(json.loads(row["record_json"])) for row in rows]


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    route = record.get("route") or {}
    metrics = record.get("metrics") or {}
    costs = metrics.get("cost_usd") or {}
    answer = record.get("answer") or {}
    counts = schema.count_verdicts(record.get("claims") or [])
    return {
        "decision_id": record["request_id"],
        "request_id": record["request_id"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "status": record["status"],
        "mode": record["mode"],
        "question": record["request"]["question"],
        "model_tier": route.get("model"),
        "model_id": route.get("model_id"),
        "confidence": route.get("confidence"),
        "upgraded_for_confidence": route.get("upgraded_for_confidence"),
        "routing_ms": metrics.get("routing_ms"),
        "total_ms": metrics.get("total_ms"),
        "reason": route.get("reason"),
        "tokens_in": answer.get("tokens_in"),
        "tokens_out": answer.get("tokens_out"),
        "cost_usd": costs.get("total"),
        "cost_breakdown": costs,
        "supported_claims": counts["supported"],
        "unsupported_claims": counts["unsupported"],
        "unclear_claims": counts["unclear"],
        "verification_status": (record.get("verification") or {}).get("status"),
        "error": record.get("error"),
    }
