"""Smoke-test verify() on three hardcoded question/answer pairs.

Run from anywhere:  python verifier/try_verify.py

Makes real Nebius + Tavily calls, so it is deliberately tiny: 3 pairs, with
budgets of 1200 (extractor) and 2500 (judge) output tokens. Those are far above
the 150-token test rule, for a measured reason: both tiers are reasoning models
and spend most of a small budget inside <think>. Measured: extractor at 150 ->
0 visible chars, 400 -> 0 for the paper answer (646 reasoning tokens), 800 ->
3 claims. Judge at 400/1000 -> the JSON array is cut off mid-object once each
claim has 3 evidence items; 2500 leaves room for the reasoning plus the array.
Costs stay around three cents per pair, dominated by Tavily, not the models.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Evidence snippets are arbitrary web text; never crash on console encoding.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from verify import verify  # noqa: E402

EXTRACTOR_MAX_TOKENS = 1200
JUDGE_MAX_TOKENS = 2500
MAX_CLAIMS = 4
LONG_CLAIM_CHARS = 160

PAIRS = [
    (
        "correct",
        "Which river runs through the Grand Canyon, and when did the Sydney Opera House open?",
        "The Colorado River runs through the Grand Canyon. The Sydney Opera House opened in 1973.",
    ),
    (
        "wrong date",
        "When did the Berlin Wall fall?",
        "The Berlin Wall fell on 9 November 1991, after weeks of peaceful protests in East Germany.",
    ),
    (
        "fake paper",
        "What did the 2023 paper 'Recursive Latent Alignment for Sparse Routing' by Chen et al. find?",
        "Chen et al. (2023) introduced Recursive Latent Alignment for Sparse Routing and reported a "
        "23% accuracy gain on MMLU with 40% fewer FLOPs.",
    ),
]


def _key_state() -> tuple[bool, bool]:
    return (
        bool(os.getenv("NEBIUS_API_KEY", "").strip()),
        bool(os.getenv("TAVILY_API_KEY", "").strip()),
    )


def main() -> int:
    has_model_key, has_search_key = _key_state()
    if not has_model_key:
        print("NEBIUS_API_KEY is not set for this run; add it to backend/.env first.")
        return 1
    if not has_search_key:
        print("TAVILY_API_KEY is not set; every claim would come back 'unclear'.")
        return 1

    total_cost = 0.0
    observations: list[str] = []

    for number, (label, question, answer) in enumerate(PAIRS, start=1):
        print(f"\n=== pair {number}/3: {label} ===")
        print(f"Q: {question}")
        print(f"A: {answer}")

        result = verify(
            question,
            answer,
            max_claims=MAX_CLAIMS,
            extractor_max_tokens=EXTRACTOR_MAX_TOKENS,
            judge_max_tokens=JUDGE_MAX_TOKENS,
        )
        total_cost += result["cost_usd"]
        claims = result["claims"]
        print(f"claims={len(claims)} searches={result['searches']} cost=${result['cost_usd']:.6f}")

        if not claims:
            observations.append(f"pair {number} ({label}): the extractor returned no claims")
            print("  (no claims extracted)")

        for index, claim in enumerate(claims, start=1):
            print(f"  [{index}] {claim['verdict'].upper()}")
            print(f"      claim   : {claim['text']}  ({len(claim['text'])} chars)")
            print(f"      source  : {claim['source_url'] or '-'}")
            print(f"      why     : {claim['reason']}")
            if claim["evidence_snippet"]:
                print(f"      evidence: {claim['evidence_snippet'][:200]}")
            if len(claim["text"]) > LONG_CLAIM_CHARS:
                observations.append(
                    f"pair {number} ({label}): claim {index} is {len(claim['text'])} chars, "
                    "probably not atomic"
                )
            if claim["verdict"] == "supported" and not claim["source_url"]:
                observations.append(
                    f"pair {number} ({label}): claim {index} is 'supported' but cites no source"
                )

    print(f"\nTotal cost: ${total_cost:.6f}")
    print("\nWatch list:")
    if observations:
        for line in observations:
            print(f" - {line}")
    else:
        print(" - nothing flagged (no empty extractions, no over-long claims)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
