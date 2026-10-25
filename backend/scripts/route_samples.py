"""Route 10 sample prompts, save each decision, and print the reasons."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, db, router, schema  # noqa: E402

SAMPLES = [
    "Say hello in one short sentence.",
    "What is 2 + 2?",
    "Explain photosynthesis in one sentence.",
    "Write a Python function that merges two sorted lists and returns a new list.",
    "Debug this stack trace: TypeError: 'NoneType' object is not subscriptable in my FastAPI handler.",
    "Who wrote the novel Beloved?",
    "What did the latest IPCC report say about 2025 emissions, and which citations support it?",
    "Explain the trade-offs of designing a rate limiter for a multi-tenant API and recommend an approach.",
    "Solve the integral of x^2 sin(x) dx by parts and show each step.",
    "Compare Nemotron Nano, Super, and Ultra for a high-volume support bot, and recommend one with reasons.",
]


def main() -> int:
    db.init_db()
    print(f"db: {config.db_path()}\n")
    print(f"{'#':>2} {'tier':6} {'conf':>5} {'ms':>6} {'esc':5} prompt")
    print("-" * 100)

    saved = 0
    for index, prompt in enumerate(SAMPLES, start=1):
        result = router.route_prompt(prompt)
        record = schema.build_record(
            question=prompt,
            messages=[{"role": "user", "content": prompt}],
            model_requested="auto",
            mode="fast",
            routing=result,
            status="provisional",
            answer=None,
            claims=[],
            verifier="stub",
            verification_status="skipped",
            total_ms=result["routing_ms"],
            cost_base=0.0,
            cost_verification=0.0,
            cost_repair=0.0,
            max_tokens=config.default_max_tokens(),
            temperature=0.2,
        )
        db.insert_decision(record)
        saved += 1
        print(
            f"{index:>2} {result['tier']:6} {result['confidence']:5.2f} "
            f"{result['routing_ms']:6.2f} {str(result['features']['escalated']):5} {prompt[:58]}"
        )
        print(f"   reason: {result['reason']}")
        print(f"   saved:  {record['request_id']}")

    print("-" * 100)
    recent = db.list_decisions(limit=len(SAMPLES))
    print(f"routed={saved} saved_records={len(recent)}")
    return 0 if saved == len(SAMPLES) == len(recent) else 1


if __name__ == "__main__":
    raise SystemExit(main())
