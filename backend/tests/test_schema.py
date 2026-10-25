from __future__ import annotations

import pytest

from app import router, schema


def _answer(**overrides):
    answer = {
        "status": "provisional",
        "text": "[mock:super] answer",
        "model": "mock/super",
        "model_tier": "super",
        "tokens_in": 10,
        "tokens_out": 20,
        "latency_ms": 5.0,
        "finish_reason": "stop",
        "mock": True,
    }
    answer.update(overrides)
    return answer


def _record(**overrides):
    routing = router.route_prompt("Write a Python function to reverse a list.")
    record = schema.build_record(
        question="Write a Python function to reverse a list.",
        messages=[{"role": "user", "content": "Write a Python function to reverse a list."}],
        model_requested="auto",
        mode="fast",
        routing=routing,
        status="provisional",
        answer=_answer(),
        claims=[],
        verifier="stub",
        verification_status="pending",
        total_ms=6.0,
        cost_base=0.00001,
        max_tokens=512,
        temperature=0.2,
    )
    record.update(overrides)
    return record


def _claim(text: str, verdict: str = "supported"):
    return {
        "text": text,
        "verdict": verdict,
        "source_url": "https://example.com/source" if verdict == "supported" else None,
        "evidence_snippet": "snippet" if verdict == "supported" else None,
    }


def test_built_record_validates_and_has_v11_fields():
    record = _record()
    schema.validate_record(record)
    assert record["schema_version"] == "1.1"
    assert record["request_id"].startswith("dec_")
    assert record["route"]["model"] in {"nano", "super", "ultra"}
    assert record["route"]["model_id"].startswith("nvidia/")
    assert record["route"]["upgraded_for_confidence"] is False
    assert record["metrics"]["cost_usd"] == {
        "base": 0.00001,
        "verification": 0.0,
        "repair": 0.0,
        "total": 0.00001,
    }
    assert record["answer"]["status"] == "provisional"


def test_failed_record_allows_null_answer():
    record = _record(
        status="failed",
        answer=None,
        claims=[],
        error="boom",
    )
    schema.validate_record(record)
    assert record["answer"] is None


def test_missing_required_field_is_rejected():
    record = _record()
    del record["claims"]
    with pytest.raises(schema.SchemaError, match="missing required property 'claims'"):
        schema.validate_record(record)


def test_bad_enum_and_extra_property_are_rejected():
    record = _record()
    record["route"]["model"] = "mega"
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.validate_record(record)

    record = _record()
    record["unexpected"] = True
    with pytest.raises(schema.SchemaError, match="unexpected property"):
        schema.validate_record(record)


def test_invalid_verdict_is_rejected():
    record = _record(claims=[_claim("a", "probably")])
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.validate_record(record)


def test_decision_ids_are_unique_and_schema_shaped():
    ids = {schema.new_decision_id() for _ in range(100)}
    assert len(ids) == 100
    for decision_id in ids:
        assert len(decision_id) == 28
        assert decision_id[4:].isalnum()


def test_counts_helper():
    claims = [_claim("a"), _claim("b", "unsupported"), _claim("c", "unclear")]
    assert schema.count_verdicts(claims) == {"supported": 1, "unsupported": 1, "unclear": 1}


def test_repair_and_cost_breakdown():
    record = schema.build_record(
        question="What is the capital of France?",
        messages=[{"role": "user", "content": "What is the capital of France?"}],
        model_requested="nano",
        mode="verified",
        routing=router.route_prompt("What is the capital of France?"),
        status="repaired",
        answer=_answer(text="repaired text", model="mock/ultra", model_tier="ultra"),
        claims=[_claim("a")],
        repair={"model": "mock/ultra", "previous_text": "original text"},
        verifier="demo",
        verification_status="complete",
        verification_ms=12.0,
        total_ms=200.0,
        cost_base=0.00001,
        cost_verification=0.001,
        cost_repair=0.00002,
    )
    schema.validate_record(record)
    assert record["repair"] == {
        "triggered": True,
        "model": "mock/ultra",
        "previous_text": "original text",
    }
    assert record["metrics"]["cost_usd"]["total"] == pytest.approx(0.00103)
    assert record["answer"]["status"] == "repaired"
