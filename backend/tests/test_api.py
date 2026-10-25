from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import db, main, pipeline
from app import verify as verification


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _post(client: TestClient, **overrides):
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
        "mode": "fast",
        "max_tokens": 100,
    }
    body.update(overrides)
    return client.post("/v1/chat/completions", json=body)


def _wait_for_status(decision_id: str, statuses: set[str], timeout: float = 3.0):
    deadline = time.time() + timeout
    record = None
    while time.time() < deadline:
        record = db.get_decision(decision_id)
        if record and record["status"] in statuses:
            return record
        time.sleep(0.02)
    return record


def _supported_verifier(question: str, answer: str):
    return [
        {
            "text": "The answer's main claim.",
            "verdict": "supported",
            "source_url": "https://example.com/source",
            "evidence_snippet": "A supporting passage.",
        }
    ]


def _picky_verifier(question: str, answer: str):
    return [
        {
            "text": "A doubtful claim.",
            "verdict": "unsupported",
            "source_url": None,
            "evidence_snippet": None,
        }
    ]


def test_healthz_exposes_models_and_verifier_state(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["schema_version"] == "1.1"
    assert set(body["models"]) == {"nano", "super", "ultra"}
    assert body["verifier"] == "stub"
    assert body["verifier_available"] is False


def test_stream_true_is_rejected(client):
    response = _post(client, stream=True)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "streaming_not_supported"


def test_empty_messages_is_a_400_not_a_422(client):
    response = client.post("/v1/chat/completions", json={"model": "auto", "messages": []})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_messages"


def test_invalid_role_and_model_are_rejected(client):
    bad_role = _post(client, messages=[{"role": "wizard", "content": "hi"}])
    assert bad_role.status_code == 400

    bad_model = _post(client, model="gpt-4")
    assert bad_model.status_code == 400
    assert bad_model.json()["error"]["code"] == "unsupported_model"


def test_fast_mode_is_provisional_then_goes_unchecked_without_verifier(client):
    response = _post(client, mode="fast")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"]
    gateway = body["gateway"]
    assert gateway["status"] == "provisional"
    assert isinstance(gateway["supported_claims"], int)
    assert isinstance(gateway["unsupported_claims"], int)
    assert gateway["supported_claims"] == 0
    assert gateway["decision_id"].startswith("dec_")

    finished = _wait_for_status(gateway["decision_id"], {"unchecked", "verified", "repaired"})
    assert finished is not None
    assert finished["status"] == "unchecked"
    assert finished["verification"]["status"] == "skipped"
    assert finished["claims"] == []
    assert finished["answer"]["status"] == "unchecked"


def test_verified_mode_waits_and_reports_unchecked_with_stub(client):
    response = _post(
        client,
        mode="verified",
        messages=[{"role": "user", "content": "Who wrote the novel Beloved?"}],
    )
    assert response.status_code == 200
    gateway = response.json()["gateway"]
    assert gateway["status"] == "unchecked"
    record = db.get_decision(gateway["decision_id"])
    assert record["verification"]["status"] == "skipped"
    assert record["status"] == "unchecked"
    assert record["answer"]["status"] == "unchecked"


def test_supported_claims_produce_verified(client):
    verification.set_verifier(_supported_verifier)
    response = _post(client, mode="verified", model="super")
    assert response.status_code == 200
    gateway = response.json()["gateway"]
    assert gateway["status"] == "verified"
    assert gateway["supported_claims"] == 1
    assert gateway["unsupported_claims"] == 0

    record = db.get_decision(gateway["decision_id"])
    assert record["claims"][0]["verdict"] == "supported"
    assert record["claims"][0]["source_url"] == "https://example.com/source"
    assert record["claims"][0]["evidence_snippet"] == "A supporting passage."
    assert record["answer"]["status"] == "verified"
    assert record["verification"]["status"] == "complete"


def test_unsupported_claim_triggers_ultra_repair_and_recheck(client, monkeypatch):
    monkeypatch.setenv("TRUSTROUTER_VERIFY_COST_USD", "0.001")
    verification.set_verifier(_picky_verifier)
    response = _post(
        client,
        mode="verified",
        model="nano",
        messages=[{"role": "user", "content": "What is the capital of France?"}],
    )
    assert response.status_code == 200
    body = response.json()
    gateway = body["gateway"]
    assert gateway["status"] == "unchecked"
    assert body["choices"][0]["message"]["content"].startswith("[mock:ultra]")
    assert gateway["unsupported_claims"] == 1

    record = db.get_decision(gateway["decision_id"])
    assert record["repair"]["model"].startswith("mock/ultra")
    assert record["repair"]["previous_text"].startswith("[mock:nano]")
    assert record["answer"]["text"] == body["choices"][0]["message"]["content"]
    assert record["answer"]["status"] == "unchecked"
    assert record["error"] == "unsupported_claims"
    assert record["answer"]["model_tier"] == "ultra"
    assert record["metrics"]["cost_usd"]["verification"] == pytest.approx(0.002)
    assert record["metrics"]["cost_usd"]["total"] == pytest.approx(0.002)
    assert record["verification"]["status"] == "complete"
    assert record["metrics"]["total_ms"] >= 0


def test_unclear_claims_are_preserved_and_counted_separately(client):
    def unclear_verifier(question: str, answer: str):
        return [{"text": "Maybe true.", "verdict": "unclear"}]

    verification.set_verifier(unclear_verifier)
    response = _post(client, mode="verified")
    gateway = response.json()["gateway"]
    assert gateway["status"] == "verified"
    assert gateway["supported_claims"] == 0
    assert gateway["unsupported_claims"] == 0

    record = db.get_decision(gateway["decision_id"])
    assert record["claims"][0]["verdict"] == "unclear"


def test_fast_mode_verifies_in_background_with_a_real_verifier(client):
    def slow_verifier(question: str, answer: str):
        time.sleep(0.15)
        return _supported_verifier(question, answer)

    verification.set_verifier(slow_verifier)
    response = _post(client, mode="fast")
    assert response.json()["gateway"]["status"] == "provisional"
    decision_id = response.json()["gateway"]["decision_id"]

    finished = _wait_for_status(decision_id, {"verified", "repaired", "unchecked"})
    assert finished["status"] == "verified"
    assert finished["claims"][0]["verdict"] == "supported"
    assert finished["metrics"]["total_ms"] > 0


def test_explicit_model_alias_bypasses_router(client):
    response = _post(client, model="nano", mode="verified")
    assert response.status_code == 200
    record = db.get_decision(response.json()["gateway"]["decision_id"])
    assert record["route"]["model"] == "nano"
    assert record["route"]["model_id"].startswith("nvidia/")
    assert record["route"]["confidence"] == 1.0
    assert "explicit model 'nano'" in record["route"]["reason"]


def test_decision_endpoints(client):
    verification.set_verifier(_supported_verifier)
    response = _post(client, mode="verified")
    decision_id = response.json()["gateway"]["decision_id"]

    detail = client.get(f"/decisions/{decision_id}")
    assert detail.status_code == 200
    record = detail.json()
    assert record["request_id"] == decision_id
    assert record["schema_version"] == "1.1"

    listing = client.get("/decisions", params={"limit": 1})
    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] == 1
    row = body["data"][0]
    assert row["decision_id"] == decision_id
    assert row["request_id"] == decision_id
    assert row["supported_claims"] == 1
    assert row["cost_breakdown"]["total"] == row["cost_usd"]
    assert row["reason"]

    missing = client.get("/decisions/dec_000000000000000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "decision_not_found"


def test_upstream_failure_is_recorded_as_failed(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(pipeline.llm, "call_llm", boom)
    response = _post(client)
    assert response.status_code == 502
    gateway = response.json()["gateway"]
    assert gateway["status"] == "failed"
    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "failed"
    assert record["answer"] is None
    assert "upstream exploded" in record["error"]


def test_cors_allows_vite_dev_origin(client):
    response = client.options(
        "/v1/chat/completions",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
