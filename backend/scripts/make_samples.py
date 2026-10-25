"""Capture provisional, verified, repaired, and unchecked responses.

Runs entirely offline (TRUSTROUTER_MOCK_LLM=1) so C has stable payloads without
spending credits. The verified/repaired samples use scripts/demo_verifier.py to
exercise a real verifier seam; the unchecked sample leaves the stub in place.
Re-run against live models once NEBIUS_API_KEY is configured.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
SAMPLES_DIR = BACKEND_DIR / "samples"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

os.environ.setdefault("TRUSTROUTER_MOCK_LLM", "1")

from fastapi.testclient import TestClient  # noqa: E402

import demo_verifier  # noqa: E402

from app import db  # noqa: E402
from app import verify as verification  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


def _call(client: TestClient, *, prompt: str, mode: str, model: str = "auto") -> dict:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": model,
            "mode": mode,
            "max_tokens": 120,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    response.raise_for_status()
    return response.json()


def _wait_until_settled(decision_id: str, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = db.get_decision(decision_id)
        if record and record["status"] != "provisional":
            return
        time.sleep(0.02)


def _write(name: str, payload: dict) -> Path:
    path = SAMPLES_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _write_record(name: str, decision_id: str) -> Path:
    record = db.get_decision(decision_id)
    if record is None:
        raise RuntimeError(f"missing record {decision_id}")
    directory = SAMPLES_DIR / "records"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="trustrouter-samples-"))
    os.environ["TRUSTROUTER_DB"] = str(workdir / "decisions.db")
    os.environ["TRUSTROUTER_MOCK_LLM"] = "1"
    os.environ["TRUSTROUTER_VERIFY_COST_USD"] = "0.001"
    verification.set_verifier(None)

    with TestClient(fastapi_app) as client:
        provisional = _call(client, prompt="Say hello in one short sentence.", mode="fast")
        _wait_until_settled(provisional["gateway"]["decision_id"])

        verification.set_verifier(demo_verifier.verify_all_supported)
        verified = _call(
            client,
            prompt="Who wrote the novel Beloved?",
            mode="verified",
            model="super",
        )

        verification.set_verifier(demo_verifier.verify)
        repaired = _call(
            client,
            prompt="What is the capital of France?",
            mode="verified",
            model="nano",
        )

        verification.set_verifier(None)
        unchecked = _call(
            client,
            prompt="How many moons does Neptune have?",
            mode="verified",
            model="nano",
        )

    outputs = {
        "provisional": _write("provisional", provisional),
        "verified": _write("verified", verified),
        "repaired": _write("repaired", repaired),
        "unchecked": _write("unchecked", unchecked),
    }
    record_paths = {
        "verified": _write_record("verified", verified["gateway"]["decision_id"]),
        "repaired": _write_record("repaired", repaired["gateway"]["decision_id"]),
        "unchecked": _write_record("unchecked", unchecked["gateway"]["decision_id"]),
    }

    for name, path in outputs.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        gateway = payload["gateway"]
        print(
            f"{name:12} status={gateway['status']:11} tier={gateway['model_tier']:5} "
            f"supported={gateway['supported_claims']} unsupported={gateway['unsupported_claims']} "
            f"decision={gateway['decision_id']}"
        )
    print(f"\nrecords stored under {workdir}")
    for name, path in record_paths.items():
        print(f"record {name:10} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
