from __future__ import annotations

import pytest

from app import config, router, schema

SAMPLES = [
    ("Say hello in one short sentence.", "nano"),
    ("What is 2 + 2?", "nano"),
    ("Write a Python function that merges two sorted lists and returns a new list.", "super"),
    (
        "Debug this stack trace: TypeError: 'NoneType' object is not subscriptable "
        "in my FastAPI handler.",
        "super",
    ),
    ("Who wrote the novel Beloved?", "super"),
    (
        "What did the latest IPCC report say about 2025 emissions, and which citations "
        "support it?",
        "ultra",
    ),
    (
        "Explain the trade-offs of designing a rate limiter for a multi-tenant API and "
        "recommend an approach.",
        "super",
    ),
    ("Solve the integral of x^2 sin(x) dx by parts and show each step.", "ultra"),
    (
        "Compare Nemotron Nano, Super, and Ultra for a high-volume support bot, and "
        "recommend one with reasons.",
        "super",
    ),
]


@pytest.mark.parametrize("prompt,expected_tier", SAMPLES)
def test_sample_prompts_route_to_expected_tier(prompt, expected_tier):
    result = router.route_prompt(prompt)
    assert result["tier"] == expected_tier
    assert result["model"] == config.model_for_tier(expected_tier)
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["routing_ms"] >= 0
    assert expected_tier in result["reason"]


def test_short_prompt_without_signals_stays_nano():
    result = router.route_prompt("hi")
    assert result["tier"] == "nano"
    assert result["features"]["score"] < router.NANO_THRESHOLD
    assert result["features"]["escalated"] is False


def test_low_confidence_escalates_one_tier():
    # One reasoning hit and no length padding lands just under the nano boundary.
    result = router.route_prompt("Explain photosynthesis in one sentence.")
    assert result["confidence"] < router.CONFIDENCE_FLOOR
    assert result["features"]["base_tier"] == "nano"
    assert result["tier"] == "super"
    assert result["features"]["escalated"] is True
    assert "escalated nano->super" in result["reason"]


def test_factual_signals_detected():
    result = router.route_prompt(
        "According to the 2024 report, what is the latest population of Tokyo?"
    )
    assert result["features"]["factual_risk"] is True
    assert {"date:2024", "recency:latest"} <= set(result["features"]["factual_hits"])


def test_features_match_shared_schema_keys():
    result = router.route_prompt("Write a Python function to reverse a list.")
    feature_schema = schema.load_schema()["$defs"]["features"]
    assert set(result["features"]) == set(feature_schema["required"])
    assert set(result) == {"model", "tier", "confidence", "reason", "features", "routing_ms"}
