from __future__ import annotations

import pytest

from app import verify


def test_normalizes_verdicts_sources_and_snippets():
    claims = verify.normalize_claims(
        [
            {
                "text": "Paris is in France.",
                "verdict": "supported",
                "source_url": "https://example.com/paris",
                "evidence_snippet": "Paris is the capital.",
            },
            {"text": "The moon is cheese.", "supported": False},
            {"claim": "Something uncertain.", "verdict": "unverified"},
            "A bare string claim.",
        ]
    )
    assert [claim["verdict"] for claim in claims] == [
        "supported",
        "unsupported",
        "unclear",
        "unclear",
    ]
    assert claims[0]["source_url"] == "https://example.com/paris"
    assert claims[0]["evidence_snippet"] == "Paris is the capital."
    assert claims[1]["source_url"] is None
    assert claims[2]["evidence_snippet"] is None


def test_accepts_wrapper_and_aliases():
    claims = verify.normalize_claims(
        {
            "claims": [
                {"statement": "Alias text", "verdict": "refuted", "url": "https://x.test/a"}
            ]
        }
    )
    assert claims == [
        {
            "text": "Alias text",
            "verdict": "unsupported",
            "source_url": "https://x.test/a",
            "evidence_snippet": None,
        }
    ]


def test_verifier_availability_distinguishes_stub_from_custom():
    verify.set_verifier(None)
    available, name, reason = verify.availability()
    assert available is False
    assert name == "stub"
    assert reason

    def custom(question: str, answer: str):
        return []

    verify.set_verifier(custom)
    assert verify.availability()[0] is True
    assert verify.verifier_name() == "custom"


def test_broken_env_target_is_unavailable(monkeypatch):
    verify.set_verifier(None)
    monkeypatch.setenv("TRUSTROUTER_VERIFIER", "does.not.exist:verify")
    available, name, reason = verify.availability()
    assert available is False
    assert name == "does.not.exist:verify"
    assert "ModuleNotFoundError" in reason


def test_bad_verifier_return_type_raises():
    with pytest.raises(TypeError):
        verify.normalize_claims(42)
