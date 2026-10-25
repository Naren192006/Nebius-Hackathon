"""Score the claim verifier against hand-labelled claims.

Input  (default) verifier/labeled_claims.jsonl, one JSON object per line:
    {"claim": "The Berlin Wall fell in 1989.", "label": "true"}
label is one of "true", "false", "fake".

For every claim this runs the production path - Tavily search, then the judge
prompt from prompts/judge.txt - and compares the verdict with your label.

The judge is batched: one model call per BATCH claims (default 5, which is also
the hard cap), so a 30-row file costs 6 judge calls plus 30 searches instead of
30 judge calls. Each batch prints its token counts and cost, and the totals at
the end include Tavily's per-search price.

It spends real credits, so it prints the plan and the estimate first and asks
before calling anything:

    python verifier/eval_verifier.py            # show the plan, then confirm
    python verifier/eval_verifier.py --dry-run  # show the plan, never call
    python verifier/eval_verifier.py --yes      # no prompt (CI / batch use)

Results go to verifier/eval_results.csv and the report to stdout.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

# Evidence snippets are arbitrary web text; never crash on console encoding.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"  # llm.py lives here, next to the shared .env
for _candidate in (str(HERE), str(BACKEND)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from llm import call_llm  # noqa: E402
from prompts.load import build, claims_block  # noqa: E402
from search import tavily_configured  # noqa: E402
from verify import (  # noqa: E402
    _apply_judgement,
    _blank_result,
    _first_json_array,
    _search_all,
    _search_cost,
)

LABELS = ("true", "false", "fake")
VERDICTS = ("supported", "unsupported", "unclear")

# A "true" claim must be confirmed; a "false" one must be contradicted; a "fake"
# entity has nothing to contradict it, so the honest verdict is "unclear".
STRICT = {
    "true": {"supported"},
    "false": {"unsupported"},
    "fake": {"unclear"},
}
# Treating "unsupported" as acceptable on fake rows too - some fake claims do
# drag up evidence for a similarly named real thing, and that is still a pass.
LENIENT = {
    "true": {"supported"},
    "false": {"unsupported"},
    "fake": {"unsupported", "unclear"},
}

BATCH_SIZE = 5  # one judge call per 5 claims, hard cap
JUDGE_TIER = "ultra"  # must stay in step with verify.verify()
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 4000  # 5 claims x 3 sources; measured judge needs ~200/claim
EVAL_QUESTION = "Fact-check these standalone claims. There is no surrounding answer."

# Rough per-call token shape, used only for the pre-flight estimate. The run
# itself reports measured usage, so these never touch the real cost.
EST_IN_BASE = 400
EST_IN_PER_CLAIM = 520  # claim line + 3 snippets of up to 500 chars
EST_OUT_PER_CLAIM = 460  # short reason plus the JSON for one claim
# .env.example ships zeros, so fall back to the published Nemotron prices.
DEFAULT_PRICES = {"nano": (0.06, 0.24), "super": (0.30, 0.90), "ultra": (1.00, 3.00)}


def price_for(tier: str) -> tuple[float, float, str]:
    """(usd_in, usd_out, source) per 1M tokens for a tier."""
    pair = []
    for key in (f"PRICE_{tier.upper()}_IN", f"PRICE_{tier.upper()}_OUT"):
        raw = os.getenv(key, "").strip()
        try:
            value = float(raw) if raw else 0.0
        except ValueError:
            value = 0.0
        pair.append(value if value > 0 else None)
    if None in pair:
        return (*DEFAULT_PRICES[tier], "built-in defaults (.env prices are 0)")
    return (pair[0], pair[1], "from .env")


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Create it with one JSON object per line: "
            '{"claim": "...", "label": "true|false|fake"}'
        )

    rows: list[dict] = []
    problems: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {number}: not valid JSON ({exc.msg})")
            continue
        if not isinstance(item, dict):
            problems.append(f"line {number}: expected an object")
            continue
        claim = " ".join(str(item.get("claim") or "").split())
        label = str(item.get("label") or "").strip().lower()
        if not claim:
            problems.append(f"line {number}: empty claim")
            continue
        if label not in LABELS:
            problems.append(f'line {number}: label {item.get("label")!r} is not one of {", ".join(LABELS)}')
            continue
        rows.append({"line": number, "claim": claim, "label": label})

    if problems:
        detail = "\n  ".join(problems)
        raise SystemExit(f"{path} has unusable rows:\n  {detail}")
    if not rows:
        raise SystemExit(f"{path} has no rows")
    return rows


def estimate(rows: list[dict], batch_size: int, judge_max_tokens: int) -> dict:
    rate_in, rate_out, source = price_for(JUDGE_TIER)
    per_search = _search_cost()
    calls = (len(rows) + batch_size - 1) // batch_size

    # Split the rows into the same batches the run will use.
    typical_out = 0
    typical_in = 0
    for start in range(0, len(rows), batch_size):
        size = len(rows[start : start + batch_size])
        typical_in += EST_IN_BASE + EST_IN_PER_CLAIM * size
        typical_out += EST_OUT_PER_CLAIM * size

    judge_typical = (typical_in * rate_in + typical_out * rate_out) / 1_000_000
    judge_worst = (
        typical_in * rate_in + calls * judge_max_tokens * rate_out
    ) / 1_000_000
    search_total = len(rows) * per_search
    return {
        "calls": calls,
        "rate_in": rate_in,
        "rate_out": rate_out,
        "price_source": source,
        "search_total": search_total,
        "per_search": per_search,
        "judge_typical": judge_typical,
        "judge_worst": judge_worst,
        "total_typical": search_total + judge_typical,
        "total_worst": search_total + judge_worst,
    }


def print_plan(path: Path, rows: list[dict], batch_size: int, plan: dict) -> None:
    counts = {label: sum(1 for r in rows if r["label"] == label) for label in LABELS}
    breakdown = ", ".join(f"{label} {counts[label]}" for label in LABELS)
    print(f"Input:    {path} ({len(rows)} claims: {breakdown})")
    if len(rows) <= 6:
        print("          note: 6 or fewer rows - if this is still the template, replace it first.")
    print(
        f"Plan:     {len(rows)} Tavily searches + {plan['calls']} judge call"
        f"{'' if plan['calls'] == 1 else 's'} "
        f"(batches of {batch_size}, {JUDGE_TIER}, max {JUDGE_MAX_TOKENS} output tokens)"
    )
    print(
        f"Estimate: ~${plan['total_typical']:.4f} typical, "
        f"~${plan['total_worst']:.4f} worst case at the token cap"
    )
    print(
        f"            search ${plan['search_total']:.4f} "
        f"({len(rows)} x ${plan['per_search']:.6f})"
        f" + judge ~${plan['judge_typical']:.4f}"
    )
    print(
        f"Prices:   {JUDGE_TIER} ${plan['rate_in']:.2f}/${plan['rate_out']:.2f} per 1M tokens "
        f"({plan['price_source']})"
    )


def judge_batch(claims: list[str], evidence: list[list[dict]], max_tokens: int):
    messages = build(
        "judge", question=EVAL_QUESTION, claims_block=claims_block(claims, evidence)
    )
    return call_llm(
        JUDGE_TIER, messages, temperature=JUDGE_TEMPERATURE, max_tokens=max_tokens
    )


def evaluate(rows: list[dict], batch_size: int, max_tokens: int) -> tuple[list[dict], dict]:
    results: list[dict] = []
    cost = 0.0
    searches = 0
    batches = (len(rows) + batch_size - 1) // batch_size

    for index, start in enumerate(range(0, len(rows), batch_size), 1):
        group = rows[start : start + batch_size]
        claims = [row["claim"] for row in group]

        print(f"\n[{index}/{batches}] searching {len(claims)} claim(s) ...", flush=True)
        evidence = _search_all(claims)
        searches += len(claims)
        missing = sum(1 for item in evidence if not item)
        if missing:
            print(f"          {missing} claim(s) came back with no evidence", flush=True)

        judgment = judge_batch(claims, evidence, max_tokens)
        cost += judgment.cost_usd + len(claims) * _search_cost()
        print(
            f"          judge: {judgment.tokens_in} in / {judgment.tokens_out} out, "
            f"${judgment.cost_usd:.6f}, {judgment.latency_ms:.0f} ms"
            f" (finish={judgment.finish_reason})",
            flush=True,
        )

        judged = _first_json_array(judgment.text)
        if judged is None:
            note = (
                "The judge output hit the token limit before the array closed."
                if judgment.finish_reason == "length"
                else "The judge output could not be parsed."
            )
            print(f"          ! {note} Marking this batch unclear.", flush=True)
            verdicts = [_blank_result(claim, note) for claim in claims]
        else:
            verdicts = _apply_judgement(claims, evidence, judged)

        for row, verdict in zip(group, verdicts):
            verdict["label"] = row["label"]
            results.append(verdict)
            ok = verdict["verdict"] in STRICT[row["label"]]
            print(
                f"  {'ok  ' if ok else 'MISS'} {row['label']:<5} -> "
                f"{verdict['verdict']:<11} {row['claim'][:88]}"
            )

    return results, {"cost_usd": round(cost, 6), "searches": searches}


def summarise(results: list[dict]) -> dict:
    matrix = {
        label: {verdict: 0 for verdict in VERDICTS} for label in LABELS
    }
    for item in results:
        matrix[item["label"]][item["verdict"]] += 1

    totals = {label: sum(matrix[label].values()) for label in LABELS}
    agree_strict = sum(
        matrix[label][verdict]
        for label in LABELS
        for verdict in STRICT[label]
    )
    agree_lenient = sum(
        matrix[label][verdict]
        for label in LABELS
        for verdict in LENIENT[label]
    )
    false_alarms = sum(
        matrix["true"][verdict] for verdict in ("unsupported", "unclear")
    )
    missed_false = matrix["false"]["supported"]
    missed_fake = matrix["fake"]["supported"]
    return {
        "matrix": matrix,
        "totals": totals,
        "n": len(results),
        "agree_strict": agree_strict,
        "agree_lenient": agree_lenient,
        "false_alarms": false_alarms,
        "missed_false": missed_false,
        "missed_fake": missed_fake,
        "misses": missed_false + missed_fake,
        "negatives": totals["false"] + totals["fake"],
    }


def _rate(hit: int, total: int) -> str:
    if total == 0:
        return "n/a (no rows)"
    return f"{hit}/{total} = {100.0 * hit / total:.1f}%"


def print_report(summary: dict, cost: dict, out: Path) -> None:
    matrix = summary["matrix"]
    width = max(len(label) for label in LABELS + ("label",))

    print("\n=== confusion matrix (row = your label, column = verdict) ===")
    header = f"{'label':<{width}}  " + "  ".join(f"{v:>11}" for v in VERDICTS) + "  total"
    print(header)
    print("-" * len(header))
    for label in LABELS:
        cells = "  ".join(f"{matrix[label][v]:>11}" for v in VERDICTS)
        print(f"{label:<{width}}  {cells}  {summary['totals'][label]:>5}")

    print("\n=== rates ===")
    print(
        f"agreement (strict)  {_rate(summary['agree_strict'], summary['n'])}"
        f"   [true->supported, false->unsupported, fake->unclear]"
    )
    print(
        f"agreement (lenient) {_rate(summary['agree_lenient'], summary['n'])}"
        f"   [fake also passes as unsupported]"
    )
    print(
        f"false-alarm rate    {_rate(summary['false_alarms'], summary['totals']['true'])}"
        f"   [true claims called unsupported or unclear]"
    )
    print(
        f"miss rate           {_rate(summary['misses'], summary['negatives'])}"
        f"   [false or fake claims called supported]"
    )
    print(f"   false -> supported {_rate(summary['missed_false'], summary['totals']['false'])}")
    print(f"   fake  -> supported {_rate(summary['missed_fake'], summary['totals']['fake'])}")
    print(
        f"\nspend: ${cost['cost_usd']:.6f} over {cost['searches']} searches "
        f"and {summary['n']} claims"
    )
    print(f"rows written: {out}")


def write_csv(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["claim", "label", "verdict", "agrees", "reason", "source_url"],
        )
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "claim": item["text"],
                    "label": item["label"],
                    "verdict": item["verdict"],
                    "agrees": "yes" if item["verdict"] in STRICT[item["label"]] else "no",
                    "reason": item["reason"],
                    "source_url": item["source_url"] or "",
                }
            )


def confirm(plan: dict, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    prompt = f"\nSpend up to ~${plan['total_worst']:.4f} on these calls? [y/N] "
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        print("\nNo terminal to confirm on; nothing was called. Re-run with --yes to skip this.")
        return False
    return answer in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the verifier against hand-labelled claims.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default=str(HERE / "labeled_claims.jsonl"))
    parser.add_argument("--out", default=str(HERE / "eval_results.csv"))
    parser.add_argument(
        "--batch",
        type=int,
        default=BATCH_SIZE,
        help=f"claims per judge call, 1..{BATCH_SIZE}",
    )
    parser.add_argument(
        "--judge-max-tokens",
        type=int,
        default=JUDGE_MAX_TOKENS,
        help="output token cap per judge call",
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = parser.parse_args(argv)

    if not 1 <= args.batch <= BATCH_SIZE:
        raise SystemExit(f"--batch must be between 1 and {BATCH_SIZE} (one judge call per 5 claims)")
    if args.judge_max_tokens < 200:
        raise SystemExit("--judge-max-tokens below 200 will truncate the JSON array")

    in_path = Path(args.input).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    rows = load_rows(in_path)

    plan = estimate(rows, args.batch, args.judge_max_tokens)
    print_plan(in_path, rows, args.batch, plan)
    if args.dry_run:
        print("\n--dry-run: nothing was called.")
        return 0

    if not os.getenv("NEBIUS_API_KEY", "").strip():
        raise SystemExit("\nNEBIUS_API_KEY is not set; add it to backend/.env first.")
    if not tavily_configured():
        raise SystemExit(
            "\nTAVILY_API_KEY is not set; every claim would come back unclear. "
            "Add it to backend/.env first."
        )

    if not confirm(plan, args.yes):
        print("No calls were made.")
        return 0

    results, cost = evaluate(rows, args.batch, args.judge_max_tokens)
    summary = summarise(results)
    write_csv(results, out_path)
    print_report(summary, cost, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
