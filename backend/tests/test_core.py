"""Regression tests for the zero-claim verification bug.

A long answer that produces no claims was being reported as `verified`, because
the pipeline only counted unsupported claims and never asked whether anything
was checked at all. These tests pin the three outcomes:

    long answer + no claims        -> unchecked (extraction failed)
    extractor output unparseable   -> unchecked, and the raw text is logged
    claims present and supported   -> verified
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, llm, main, pipeline
from app import verify as verification

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_DIR = REPO_ROOT / "trustrouter" / "verifier"
if str(VERIFIER_DIR) not in sys.path:
    sys.path.insert(0, str(VERIFIER_DIR))

import verify as claim_verifier  # noqa: E402  (trustrouter/verifier/verify.py)

# 449 characters: comfortably over pipeline.MIN_CHECKABLE_ANSWER_CHARS.
LONG_ANSWER = (
    "The Sydney Opera House opened in 1973, seven years behind schedule and far over budget. "
    "Jorn Utzon won the design competition in 1957 with a scheme of sail-like shells, but the "
    "roof geometry was not solved until 1961, and building the shells consumed most of the next "
    "decade. The building finally opened on 20 October 1973 with Queen Elizabeth II attending, "
    "and it has since become the most recognisable landmark in Australia."
)


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _post(client: TestClient, **overrides):
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": "Tell me about the Sydney Opera House."}],
        "mode": "verified",
        "max_tokens": 150,
    }
    body.update(overrides)
    return client.post("/v1/chat/completions", json=body)


def _answer(text: str):
    """A fake tier call that returns `text`, so answer length is controlled."""

    def _call(tier, messages, *, max_tokens=None, temperature=0.2):
        return llm.LLMResult(
            text=text,
            tokens_in=20,
            tokens_out=max(1, len(text) // 4),
            latency_ms=1.0,
            cost_usd=0.0,
            model=f"stub/{tier}",
            tier=tier,
            finish_reason="stop",
            mock=True,
        )

    return _call


def _no_claims(question: str, answer: str):
    return []


def _unsupported_claim(question: str, answer: str):
    return [
        {
            "text": "The Opera House opened in 1959.",
            "verdict": "unsupported",
            "source_url": None,
            "evidence_snippet": None,
        }
    ]


def test_long_answer_with_zero_claims_is_unchecked(client, monkeypatch):
    assert len(LONG_ANSWER) > pipeline.MIN_CHECKABLE_ANSWER_CHARS
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer(LONG_ANSWER))
    verification.set_verifier(_no_claims)

    response = _post(client)
    assert response.status_code == 200
    gateway = response.json()["gateway"]
    assert gateway["status"] == "unchecked"
    assert gateway["supported_claims"] == 0
    assert gateway["unsupported_claims"] == 0

    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "unchecked"
    assert record["answer"]["status"] == "unchecked"
    assert record["answer"]["text"] == LONG_ANSWER
    assert record["claims"] == []
    assert record["error"]
    assert "no claims" in record["error"]
    assert str(len(LONG_ANSWER)) in record["error"]
    assert record["verification"]["status"] == "complete"
    assert record["verification"]["error"] == "no claims were extracted from the answer"


# Exactly 600 characters, cut to length on purpose: this is the boundary case
# where a long answer yields no claims and must not be reported as verified.
SIX_HUNDRED_CHAR_ANSWER = (
    "The Colorado River rises in the Rocky Mountains of Colorado and runs about 1,450 miles "
    "to the Gulf of California, crossing five states and carving the Grand Canyon on the way. "
    "Its water is divided among seven basin states by the Colorado River Compact of 1922, and "
    "dams at Hoover and Glen Canyon regulate a flow that now rarely reaches the sea. "
    "The river supplies drinking water to roughly forty million people and irrigates more than "
    "four million acres of farmland in the United States and Mexico. Salt buildup and drought "
    "have cut its volume sharply since the reservoirs were filled in the twentieth century."
)[:600]


def test_six_hundred_character_answer_with_zero_claims_is_unchecked(client, monkeypatch):
    assert len(SIX_HUNDRED_CHAR_ANSWER) == 600
    assert len(SIX_HUNDRED_CHAR_ANSWER) > pipeline.MIN_CHECKABLE_ANSWER_CHARS
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer(SIX_HUNDRED_CHAR_ANSWER))
    verification.set_verifier(_no_claims)

    response = _post(client)
    assert response.status_code == 200
    gateway = response.json()["gateway"]
    assert gateway["status"] == "unchecked"
    assert gateway["supported_claims"] == 0
    assert gateway["unsupported_claims"] == 0

    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "unchecked"
    assert record["answer"]["status"] == "unchecked"
    assert record["answer"]["text"] == SIX_HUNDRED_CHAR_ANSWER
    assert record["claims"] == []
    assert record["error"]
    assert "600-character answer" in record["error"]
    assert record["verification"]["status"] == "complete"
    assert record["verification"]["error"] == "no claims were extracted from the answer"


def test_unparseable_extractor_output_is_unchecked_and_logged(client, monkeypatch, tmp_path):
    log_path = tmp_path / "extractor_failures.jsonl"
    monkeypatch.setenv(claim_verifier.EXTRACTOR_LOG_ENV, str(log_path))
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer(LONG_ANSWER))
    monkeypatch.setattr(
        claim_verifier,
        "call_llm",
        lambda *args, **kwargs: SimpleNamespace(
            text="I cannot produce that JSON for this answer.", cost_usd=0.0
        ),
    )
    verification.set_verifier(claim_verifier.verify)

    response = _post(client)
    gateway = response.json()["gateway"]
    assert gateway["status"] == "unchecked"

    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "unchecked"
    assert record["claims"] == []
    assert "no claims" in record["error"]

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry) == {
        "ts", "reason", "answer_chars", "raw_chars", "parsed_items", "answer", "raw"
    }
    assert entry["reason"] == "extractor_unparseable"
    assert entry["answer"] == LONG_ANSWER
    assert entry["answer_chars"] == len(LONG_ANSWER)
    assert entry["parsed_items"] is None
    assert "I cannot produce that JSON" in entry["raw"]
    # Only those fields are recorded: the answer and extractor output, no
    # question text and no environment values.
    assert "api_key" not in lines[0].lower()


def test_supported_claims_on_a_long_answer_stay_verified(client, monkeypatch):
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer(LONG_ANSWER))

    def supported(question: str, answer: str):
        return [
            {
                "text": "The Sydney Opera House opened in 1973.",
                "verdict": "supported",
                "source_url": "https://example.com/opera-house",
                "evidence_snippet": "The Opera House opened in October 1973.",
            }
        ]

    verification.set_verifier(supported)
    response = _post(client)
    gateway = response.json()["gateway"]
    assert gateway["status"] == "verified"
    assert gateway["supported_claims"] == 1

    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "verified"
    assert record["answer"]["status"] == "verified"
    assert record["claims"][0]["verdict"] == "supported"
    assert record["error"] is None
    assert record["verification"]["error"] is None


def test_short_answer_with_zero_claims_is_nothing_to_check(client, monkeypatch):
    """A greeting has nothing to check, so it is not reported as verified."""
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer("Hello! How can I help?"))
    verification.set_verifier(_no_claims)

    response = _post(client)
    gateway = response.json()["gateway"]
    assert gateway["status"] == "nothing_to_check"
    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "nothing_to_check"
    assert record["answer"]["status"] == "nothing_to_check"
    assert record["claims"] == []
    assert record["error"] is None
    # The verifier did run; it simply found nothing. That is not a failure.
    assert record["verification"]["status"] == "complete"
    assert record["verification"]["error"] is None


def test_factual_prompt_with_zero_claims_is_unchecked(client, monkeypatch):
    """A factual question that yields no claims was not checked, so not nothing_to_check."""
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer("No idea."))
    verification.set_verifier(_no_claims)

    prompt = "Who wrote the 1987 novel 'The Glass Lighthouse of Tamsin Vale'?"
    response = _post(client, messages=[{"role": "user", "content": prompt}])
    gateway = response.json()["gateway"]
    assert gateway["status"] == "unchecked"

    record = db.get_decision(gateway["decision_id"])
    assert record["route"]["features"]["factual_risk"] is True
    assert record["claims"] == []
    assert record["status"] == "unchecked"
    assert record["answer"]["status"] == "unchecked"
    assert record["error"] == "no_claims_extracted"


def test_greeting_with_zero_claims_stays_nothing_to_check(client, monkeypatch):
    """No factual signal in the prompt, so zero claims really is nothing to check."""
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer("Hello! How can I help?"))
    verification.set_verifier(_no_claims)

    response = _post(client, messages=[{"role": "user", "content": "Hello there!"}])
    gateway = response.json()["gateway"]
    assert gateway["status"] == "nothing_to_check"

    record = db.get_decision(gateway["decision_id"])
    assert record["route"]["features"]["factual_risk"] is False
    assert record["claims"] == []
    assert record["status"] == "nothing_to_check"
    assert record["error"] is None


def test_one_unsupported_claim_is_unchecked_even_after_repair(client, monkeypatch):
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer(LONG_ANSWER))
    verification.set_verifier(_unsupported_claim)

    response = _post(client)
    gateway = response.json()["gateway"]
    assert gateway["status"] == "unchecked"
    assert gateway["unsupported_claims"] == 1

    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "unchecked"
    assert record["answer"]["status"] == "unchecked"
    assert record["error"] == "unsupported_claims"
    # The repair pass still ran; it just could not make the claim supportable.
    assert record["repair"]["triggered"] is True


def test_repair_failure_is_unchecked_and_says_so(client, monkeypatch):
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer(LONG_ANSWER))
    verification.set_verifier(_unsupported_claim)

    def boom(record, claims):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(pipeline, "_attempt_repair", boom)

    response = _post(client)
    gateway = response.json()["gateway"]
    assert gateway["status"] == "unchecked"

    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "unchecked"
    assert record["answer"]["status"] == "unchecked"
    assert record["error"] == "repair_failed"
    # No repair pass ran, so the repair block stays null rather than half-filled.
    assert record["repair"] is None


def test_failed_recheck_after_repair_is_unchecked(client, monkeypatch):
    """Repair succeeded but the re-check blew up: the rewrite is unproven."""
    monkeypatch.setattr(pipeline.llm, "call_llm", _answer(LONG_ANSWER))

    calls = {"n": 0}

    def flaky(question: str, answer: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return _unsupported_claim(question, answer)
        raise RuntimeError("recheck exploded")

    verification.set_verifier(flaky)

    response = _post(client)
    gateway = response.json()["gateway"]
    assert gateway["status"] == "unchecked"

    record = db.get_decision(gateway["decision_id"])
    assert record["status"] == "unchecked"
    assert record["answer"]["status"] == "unchecked"
    assert record["error"] == "recheck_failed"
    # The repair did happen; only the proof of it is missing.
    assert record["repair"]["triggered"] is True
    assert "re-verification failed" in record["verification"]["error"]
    assert calls["n"] == 2


def test_extractor_parser_handles_think_blocks_fences_and_prose():
    raw = (
        "<thinking>three claims, listing them</thinking>"
        "Here is the array:\n"
        "```json\n"
        '[{"text": "The Opera House opened in 1973."}, {"text": "Utzon designed it."}]\n'
        "```\n"
        "Hope that helps."
    )
    parsed = claim_verifier._first_json_array(raw)
    assert parsed == [
        {"text": "The Opera House opened in 1973."},
        {"text": "Utzon designed it."},
    ]


def test_extractor_parser_takes_the_first_complete_array_and_gives_up_cleanly():
    assert claim_verifier._first_json_array('prose before [1, 2] prose after [3]') == [1, 2]
    assert claim_verifier._first_json_array("[1, 2") is None
    assert claim_verifier._first_json_array("<thinking>cut off mid") is None
    assert claim_verifier._first_json_array("") is None
