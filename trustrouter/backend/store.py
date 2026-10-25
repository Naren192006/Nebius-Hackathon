"""SQLite decision log + record builder (matches ../schema/decision_record.schema.json)."""
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "trustrouter.db")


def _db():
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with closing(_db()) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY, created_at TEXT, status TEXT, record TEXT)""")
        db.commit()


def build_record(prompt: str, route: dict, result, mode: str = "fast",
                 ultra_baseline_estimate: float | None = None) -> dict:
    """result is llm.LLMResult. Verification/repair fields are filled in later by B's code."""
    return {
        "request_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "features": route["features"],
        "route": {k: route[k] for k in
                  ("model", "initial_model", "confidence", "reason", "upgraded_for_confidence")},
        "answer": {"status": "provisional", "text": result.text},
        "claims": [],
        "repair": {"triggered": False, "model": None, "previous_text": None,
                   "flagged_claims": [], "claims_still_flagged": 0},
        "metrics": {
            "routing_ms": route["routing_ms"],
            "total_ms": result.latency_ms + route["routing_ms"],
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "cost_usd": {"base": result.cost_usd, "verification": 0.0, "repair": 0.0,
                         "total": result.cost_usd},
            "ultra_baseline_cost_usd_estimate": ultra_baseline_estimate,
        },
        "error": None,
    }


def save_record(rec: dict) -> None:
    init_db()
    with closing(_db()) as db:
        db.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?)",
                   (rec["request_id"], rec["created_at"], rec["answer"]["status"], json.dumps(rec)))
        db.commit()


def get_record(request_id: str) -> dict | None:
    init_db()
    with closing(_db()) as db:
        row = db.execute("SELECT record FROM decisions WHERE id=?", (request_id,)).fetchone()
    return json.loads(row[0]) if row else None


def list_records(limit: int = 50) -> list[dict]:
    init_db()
    with closing(_db()) as db:
        rows = db.execute("SELECT record FROM decisions ORDER BY created_at DESC LIMIT ?",
                          (limit,)).fetchall()
    return [json.loads(r[0]) for r in rows]
