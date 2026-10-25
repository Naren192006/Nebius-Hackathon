"""Demo-only heuristic verifier for offline sample generation.

This is NOT Backend B. It exists so scripts/make_samples.py can produce
verified/repaired payloads before the real verifier lands. Every sentence is a
claim; the mock's "local mock" sentence is rejected, everything else is
accepted with a placeholder source.
"""

from __future__ import annotations

import re

UNSUPPORTED_MARKER = "local mock"
DEMO_SOURCE = "https://example.org/trustrouter/demo-source"


def _sentences(answer: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", answer.strip()) if part.strip()]


def verify(question: str, answer: str) -> list[dict[str, object]]:
    claims: list[dict[str, object]] = []
    for sentence in _sentences(answer):
        supported = UNSUPPORTED_MARKER not in sentence.lower()
        claims.append(
            {
                "text": sentence,
                "verdict": "supported" if supported else "unsupported",
                "source_url": DEMO_SOURCE if supported else None,
                "evidence_snippet": (
                    "Demo verifier accepted this sentence." if supported else None
                ),
            }
        )
    return claims


def verify_all_supported(question: str, answer: str) -> list[dict[str, object]]:
    return [
        {
            "text": sentence,
            "verdict": "supported",
            "source_url": DEMO_SOURCE,
            "evidence_snippet": "Demo verifier accepted this sentence.",
        }
        for sentence in _sentences(answer)
    ]
