"""Smoke-test each Nemotron tier with one tiny prompt.

    python scripts/test_tiers.py --mock     # no network, no credits (run first)
    python scripts/test_tiers.py            # live, needs NEBIUS_API_KEY

Prints latency, tokens in/out, and USD cost per tier, and asserts that any
<think> blocks were stripped from the answer.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, llm  # noqa: E402

PROMPT = "Reply with one short sentence explaining what an API gateway does."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mock", action="store_true", help="use the local mock, no credits")
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument("--max-tokens", type=int, default=100)
    args = parser.parse_args()

    if args.mock:
        os.environ["TRUSTROUTER_MOCK_LLM"] = "1"
    elif not os.environ.get("NEBIUS_API_KEY", "").strip():
        print("NEBIUS_API_KEY is not set. Run with --mock, or add the key to backend/.env.", file=sys.stderr)
        return 2

    print(f"prompt: {args.prompt!r} (max_tokens={args.max_tokens})")
    print(f"mode:   {'MOCK (no credits)' if args.mock else 'LIVE'}\n")
    print(f"{'tier':6} {'model':46} {'latency_ms':>10} {'tok_in':>7} {'tok_out':>7} {'cost_usd':>11}")
    print("-" * 96)

    total_cost = 0.0
    failures = 0
    for tier in config.TIERS:
        try:
            result = llm.call_llm(
                tier, [{"role": "user", "content": args.prompt}], max_tokens=args.max_tokens
            )
        except Exception as exc:  # noqa: BLE001 - keep going so every tier is reported
            failures += 1
            print(f"{tier:6} FAILED: {type(exc).__name__}: {exc}")
            continue

        total_cost += result.cost_usd
        if "<think" in result.text.lower():
            failures += 1
            print(f"{tier:6} FAILED: <think> block was not stripped")
            continue
        if not result.text:
            failures += 1
            print(f"{tier:6} FAILED: empty answer")
            continue

        print(
            f"{tier:6} {result.model:46} {result.latency_ms:10.1f} "
            f"{result.tokens_in:7d} {result.tokens_out:7d} {result.cost_usd:11.8f}"
        )
        print(f"       answer: {result.text[:160]!r}")

    print("-" * 96)
    print(f"total cost: ${total_cost:.8f}")
    if failures:
        print(f"{failures} tier(s) failed")
        return 3
    print("all three tiers answered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
