"""Publish benchmark results as frontend/public/results.json.

Reads what run_benchmark.py wrote and produces one small JSON file the console
can fetch:

    results/summary.csv     -> strategies
    results/rows.csv        -> by_category (and the measured verification spend)
    results/failures.csv    -> failures
    results/records.jsonl   -> cost_breakdown

Shape:

    {"generated_at": "...",
     "strategies": [{...summary row...}],
     "by_category": [{"category", "strategy", "n", "accuracy",
                      "supported_claims_rate", "avg_cost_usd"}],
     "cost_breakdown": {"base": ..., "verification": ..., "repair": ...},
     "failures": [{"id", "prompt", "answer", "why"}]}

Rates and accuracies stay fractions in 0..1, exactly as summary.csv holds them;
the console multiplies by 100 when it renders a percentage. Empty cells become
null rather than 0, so "not measured" never looks like "measured as zero".

by_category uses the same comparable set as the summary: a prompt counts only
when every strategy answered it (status ok) and, where it was graded, has a
grade. Otherwise a category average would mix different prompt counts per
strategy.

cost_breakdown is the TrustRouter record split. Records price verification with
TRUSTROUTER_VERIFY_COST_USD, which is 0 by default, so when that line is 0 the
exporter substitutes the verification spend the benchmark actually measured
(verify_cost_usd in rows.csv) instead of publishing a fake zero.

No keys are read from the environment, and the output is scanned for API-key
prefixes before it is written.

    python benchmark/export_results.py
    python benchmark/export_results.py --results results --out frontend/public/results.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

REPO = Path(__file__).resolve().parent.parent
KEY_PATTERN = re.compile(r"(sk-[A-Za-z0-9]{8,}|tvly-[A-Za-z0-9]{8,}|nvapi-[A-Za-z0-9]{8,})")

SUMMARY_FIELDS = (
    "strategy", "n", "accuracy", "supported_claims_rate", "avg_cost_usd",
    "total_cost_usd", "cost_vs_ultra_pct", "p50_latency_ms", "p95_latency_ms",
    "repair_rate", "escalation_rate",
)
INT_FIELDS = {"n"}
TEXT_FIELDS = {"strategy"}


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _number(cell: str | None) -> float | int | None:
    if cell is None:
        return None
    text = cell.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _field(name: str, cell: str | None):
    if name in TEXT_FIELDS:
        return (cell or "").strip() or None
    value = _number(cell)
    if value is None:
        return None
    if name in INT_FIELDS:
        return int(value)
    return round(float(value), 8)


def build_strategies(summary_rows: list[dict]) -> list[dict]:
    strategies: list[dict] = []
    for row in summary_rows:
        if not (row.get("strategy") or "").strip():
            continue
        strategies.append({name: _field(name, row.get(name)) for name in SUMMARY_FIELDS})
    return strategies


def common_prompt_ids(rows: list[dict], strategies: list[str]) -> set[str]:
    """Prompts every strategy answered (and graded, when the prompt is graded).

    A prompt with rows for only some strategies is not comparable: counting its
    rows would give nano a larger n than ultra and move the category average.
    """
    by_prompt: dict[str, dict[str, dict]] = {}
    for row in rows:
        prompt_id = (row.get("prompt_id") or "").strip()
        strategy = (row.get("strategy") or "").strip()
        if prompt_id and strategy:
            by_prompt.setdefault(prompt_id, {})[strategy] = row

    common: set[str] = set()
    for prompt_id, per_strategy in by_prompt.items():
        if any(strategy not in per_strategy for strategy in strategies):
            continue
        group = [per_strategy[strategy] for strategy in strategies]
        if any((row.get("status") or "") != "ok" for row in group):
            continue
        graded = [(row.get("correct") or "").strip() != "" for row in group]
        if any(graded) and not all(graded):
            continue
        common.add(prompt_id)
    return common


def build_by_category(rows: list[dict], strategies: list[str]) -> list[dict]:
    common = common_prompt_ids(rows, strategies)
    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if (row.get("prompt_id") or "") not in common:
            continue
        key = ((row.get("category") or "uncategorised").strip(), (row.get("strategy") or "").strip())
        buckets.setdefault(key, []).append(row)

    categories: list[dict] = []
    for (category, strategy), group in sorted(buckets.items()):
        graded = [r for r in group if (r.get("correct") or "").strip() != ""]
        correct = sum(1 for r in graded if (r.get("correct") or "").strip().lower() == "true")
        rates = [
            value
            for value in (_number(r.get("supported_rate")) for r in group)
            if value is not None
        ]
        costs = [
            value
            for value in (_number(r.get("cost_usd")) for r in group)
            if value is not None
        ]
        categories.append(
            {
                "category": category,
                "strategy": strategy,
                "n": len(group),
                "accuracy": round(correct / len(graded), 4) if graded else None,
                "supported_claims_rate": round(sum(rates) / len(rates), 4) if rates else None,
                "avg_cost_usd": round(sum(costs) / len(costs), 8) if costs else None,
            }
        )
    return categories


def build_cost_breakdown(records_path: Path, rows: list[dict]) -> tuple[dict, str]:
    """Returns ({"base", "verification", "repair"}, where verification came from)."""
    totals = {"base": 0.0, "verification": 0.0, "repair": 0.0}
    seen = 0
    if records_path.exists():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            costs = (record.get("metrics") or {}).get("cost_usd") or {}
            for key in totals:
                totals[key] += float(costs.get(key) or 0.0)
            seen += 1

    measured = sum(
        value
        for value in (
            _number(r.get("verify_cost_usd"))
            for r in rows
            if (r.get("strategy") or "").strip() == "trustrouter"
        )
        if value is not None
    )
    source = "record"
    if totals["verification"] == 0 and measured > 0:
        totals["verification"] = measured
        source = "measured"

    detail = "record" if totals["verification"] else ("measured" if measured else "none")
    if source == "measured":
        detail = f"measured ({measured:.4f} over the benchmark rows)"
    if not seen and not measured:
        detail = "none (no records and no measured verification)"
    return {key: round(value, 8) for key, value in totals.items()}, detail


def build_failures(rows: list[dict]) -> list[dict]:
    failures: list[dict] = []
    for row in rows:
        prompt_id = (row.get("prompt_id") or "").strip()
        if not prompt_id:
            continue
        failures.append(
            {
                "id": prompt_id,
                "prompt": (row.get("prompt") or "").strip(),
                "answer": (row.get("answer") or "").strip(),
                "why": (row.get("reason") or "").strip() or "graded incorrect",
            }
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write frontend/public/results.json from the benchmark CSVs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--results", default=str(REPO / "results"), help="benchmark output dir")
    parser.add_argument(
        "--out", default=str(REPO / "frontend" / "public" / "results.json"), help="JSON to write"
    )
    args = parser.parse_args(argv)

    results_dir = Path(args.results).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()

    summary_path = results_dir / "summary.csv"
    if not summary_path.exists():
        raise SystemExit(
            f"{summary_path} not found; run benchmark/run_benchmark.py first"
        )

    summary_rows = _read_csv(summary_path)
    row_records = _read_csv(results_dir / "rows.csv")
    failure_rows = _read_csv(results_dir / "failures.csv")

    strategies = build_strategies(summary_rows)
    if not strategies:
        raise SystemExit(f"{summary_path} has no strategy rows to export")

    breakdown, breakdown_source = build_cost_breakdown(
        results_dir / "records.jsonl", row_records
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "strategies": strategies,
        "by_category": build_by_category(row_records, [s["strategy"] for s in strategies]),
        "cost_breakdown": breakdown,
        "failures": build_failures(failure_rows),
    }

    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    leak = KEY_PATTERN.search(text)
    if leak:
        raise SystemExit(
            f"refusing to write {out_path}: the export contains something that looks like an API key"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    print(f"wrote {out_path}")
    print(
        f"  strategies: {len(payload['strategies'])} rows | "
        f"by_category: {len(payload['by_category'])} rows | "
        f"failures: {len(payload['failures'])}"
    )
    print(
        "  cost_breakdown: "
        f"base ${breakdown['base']:.4f} + verification ${breakdown['verification']:.4f}"
        f" + repair ${breakdown['repair']:.4f}"
        f"  (verification: {breakdown_source})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
