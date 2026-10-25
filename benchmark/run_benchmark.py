"""Benchmark fixed tiers against TrustRouter on benchmark/prompts.jsonl.

For every prompt and every strategy this records the answer, whether it is
correct, how much of it the shared verifier could support, what it cost, and how
long it took.

Strategies
    nano / super / ultra   one direct call_llm on that tier; cost = that call.
    trustrouter            run_pipeline(mode="verified"); cost = base +
                           verification + repair, straight from the record.

Every answer with a non-empty reference is graded on the ultra tier with
verifier/prompts/grader.txt. Grading is measurement, so it is never charged to a
strategy. For the three baselines the same verifier that TrustRouter uses is run
over the answer (unless --no-baseline-verify), so supported_claims_rate is
comparable across all four strategies; that spend is measurement too, but it is
real money, so it counts against --max-usd.

Costs money: the plan and the estimate print first and nothing runs until you
type y. The walk stops as soon as --max-usd is reached.

    python benchmark/run_benchmark.py --limit 8 --max-usd 0.50
    python benchmark/run_benchmark.py --limit 8 --max-usd 0.50 --yes   # no prompt
    python benchmark/run_benchmark.py --no-baseline-verify            # cheaper
    python benchmark/run_benchmark.py --categories factual_hard,fake_entity --limit 12

--categories keeps only the named categories and interleaves them round-robin,
so --limit 12 over three categories runs four of each instead of twelve of the
first. Cached rows are skipped and never counted in the estimate.

Every (strategy, prompt) row is appended to results/cache.jsonl as soon as it
finishes, so a crash, a cap stop, or a rerun never pays for the same row twice.
A cached row is only reused when the prompt text is unchanged.

Outputs (under --out, default results/)
    rows.csv       one row per strategy x prompt
    summary.csv    per-strategy totals over prompts every strategy completed
    failures.csv   TrustRouter rows that were graded incorrect
    records.jsonl  the v1.1 decision record behind every TrustRouter row
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

REPO = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO / "backend"  # app/: pipeline, llm, verify adapter, db
VERIFIER_DIR = REPO / "trustrouter" / "verifier"  # verify(), prompts/
TRUSTROUTER_BACKEND = REPO / "trustrouter" / "backend"  # llm.py used by the verifier
for _candidate in (str(BACKEND_DIR), str(VERIFIER_DIR), str(TRUSTROUTER_BACKEND)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from app import config as gateway_config  # noqa: E402
from app import db as gateway_db  # noqa: E402
from app import llm as gateway_llm  # noqa: E402
from app import verify as gateway_verify  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402
from prompts.load import build as build_prompt  # noqa: E402
from verify import _strip_think  # noqa: E402
from verify import verify as verifier_verify  # noqa: E402

STRATEGIES = ("nano", "super", "ultra", "trustrouter")
GRADER_TIER = "ultra"
GRADER_MAX_TOKENS = 1500  # Ultra reasons first; a small cap returns no JSON
GRADER_TEMPERATURE = 0.0
MAX_WORKERS = 3  # prompts in flight at once, never more
DEFAULT_MAX_TOKENS = 1200  # above the gateway's 512 so reasoning models answer

# Estimate-only constants: the pre-flight number is a guess, every row reports
# measured usage. Measured on this verifier: ~2-3 claims, extractor ~700 output
# tokens, judge ~1600, three Tavily searches per claim.
TYPICAL_ANSWER_TOKENS = 400
EST_EXTRACTOR_IN, EST_EXTRACTOR_OUT = 200, 700
EST_JUDGE_IN, EST_JUDGE_OUT = 900, 1600
EST_SEARCHES_PER_VERIFY = 3
EST_GRADER_IN, EST_GRADER_OUT = 900, 500
EST_REPAIR_RATE = 0.3  # fraction of TrustRouter rows expected to repair
DEFAULT_PRICES = {"nano": (0.06, 0.24), "super": (0.30, 0.90), "ultra": (1.00, 3.00)}
DEFAULT_SEARCH_COST = 0.008


class BudgetExceeded(RuntimeError):
    """Raised when the next paid call would push spend past --max-usd."""


class Budget:
    """Thread-safe spend meter. Every real dollar goes through charge().

    guard() reserves the typical cost of the call it is about to allow, so a cap
    is not blown by the call that crosses it. Costs are estimates, so the final
    total can still land slightly above the cap; that gap is reported.
    """

    def __init__(self, max_usd: float, reserves: dict[str, float] | None = None) -> None:
        self.max_usd = float(max_usd)
        self.spent = 0.0
        self.reserves = reserves or {}
        self._lock = threading.Lock()
        self.reached = False

    def charge(self, usd: float) -> None:
        with self._lock:
            self.spent = round(self.spent + max(0.0, float(usd or 0.0)), 8)
            if self.spent >= self.max_usd:
                self.reached = True

    def blocked(self) -> bool:
        with self._lock:
            return self.reached or self.spent >= self.max_usd

    def guard(self, kind: str = "answer") -> None:
        reserve = float(self.reserves.get(kind, 0.0))
        with self._lock:
            if self.reached or self.spent + reserve > self.max_usd:
                raise BudgetExceeded(
                    f"spend limit reached (spent ${self.spent:.4f} of ${self.max_usd:.2f}, "
                    f"next {kind} ~${reserve:.4f})"
                )


class VerifyMeter:
    """Wrap the real verifier so TrustRouter's verification spend is measured.

    The gateway charges a flat estimate (TRUSTROUTER_VERIFY_COST_USD) per call,
    which is 0 by default. This wrapper bills the actual cost to the budget and
    sums it per thread, so each row knows what its verification really cost.
    """

    __name__ = "trustrouter.verifier"  # what decision records report as the verifier

    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.calls = 0
        self.total = 0.0  # aggregate over the whole run
        self._lock = threading.Lock()
        self._tls = threading.local()

    def reset_thread(self) -> None:
        self._tls.row_cost = 0.0
        self._tls.row_calls = 0

    def row_cost(self) -> float:
        return float(getattr(self._tls, "row_cost", 0.0))

    def row_calls(self) -> int:
        return int(getattr(self._tls, "row_calls", 0))

    def __call__(self, question: str, answer: str):
        # Refuse to start a verification the cap cannot afford. The pipeline
        # treats this like any verifier outage and marks the row unchecked.
        self.budget.guard("verify")
        result = verifier_verify(question, answer)
        cost = float(result.get("cost_usd") or 0.0) if isinstance(result, dict) else 0.0
        self._tls.row_cost = self.row_cost() + cost
        self._tls.row_calls = self.row_calls() + 1
        with self._lock:
            self.calls += 1
            self.total = round(self.total + cost, 8)
        self.budget.charge(cost)
        return result


def _price(tier: str) -> tuple[float, float]:
    try:
        return gateway_config.price_for_tier(tier)
    except Exception:  # noqa: BLE001 - missing price in .env must not stop a plan
        return DEFAULT_PRICES[tier]


def _search_cost() -> float:
    try:
        return float(os.getenv("TAVILY_COST_PER_SEARCH", "") or DEFAULT_SEARCH_COST)
    except ValueError:
        return DEFAULT_SEARCH_COST


# ---------------------------------------------------------------------------
# input


def load_prompts(path: Path, limit: int | None, categories: list[str] | None = None) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"{path} not found; run the prompt writer first")

    rows: list[dict] = []
    seen: set[str] = set()
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
        prompt_id = str(item.get("id") or "").strip()
        text = str(item.get("prompt") or "").strip()
        if not prompt_id or not text:
            problems.append(f"line {number}: missing id or prompt")
            continue
        if prompt_id in seen:
            problems.append(f"line {number}: duplicate id {prompt_id!r}")
            continue
        seen.add(prompt_id)
        rows.append(
            {
                "id": prompt_id,
                "category": str(item.get("category") or "uncategorised").strip(),
                "prompt": text,
                "reference": str(item.get("reference") or "").strip(),
            }
        )
    if problems:
        raise SystemExit(f"{path} has unusable rows:\n  " + "\n  ".join(problems))
    if not rows:
        raise SystemExit(f"{path} has no prompts")

    if categories:
        wanted = [c.strip() for c in categories if c.strip()]
        present = {row["category"] for row in rows}
        unknown = [c for c in wanted if c not in present]
        if unknown:
            raise SystemExit(
                f"--categories has no match for {unknown}; these are in the file: "
                + ", ".join(sorted(present))
            )
        # Round-robin so a --limit smaller than the category total still covers
        # every selected category instead of draining the first one.
        buckets = {c: [row for row in rows if row["category"] == c] for c in wanted}
        rows = []
        while any(buckets[c] for c in wanted):
            for category in wanted:
                if buckets[category]:
                    rows.append(buckets[category].pop(0))

    if limit is not None:
        rows = rows[: max(0, limit)]
    return rows


def prompt_sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# cache


class Cache:
    """Append-only (strategy, prompt) result log; rewriting needs are handled at the end."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.entries: dict[tuple[str, str], dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and item.get("strategy") and item.get("prompt_id"):
                    self.entries[(item["strategy"], item["prompt_id"])] = item

    # An upstream failure costs nothing and is worth another attempt; freezing it
    # into the cache would make one bad minute permanent.
    RETRYABLE = {"error", "failed"}

    def get(self, strategy: str, prompt_id: str, sha: str) -> dict | None:
        entry = self.entries.get((strategy, prompt_id))
        if entry and entry.get("prompt_sha") == sha:
            if (entry.get("row") or {}).get("status") in self.RETRYABLE:
                return None
            return entry
        return None

    def put(self, strategy: str, prompt_id: str, sha: str, row: dict, record: dict | None) -> None:
        entry = {
            "strategy": strategy,
            "prompt_id": prompt_id,
            "prompt_sha": sha,
            "row": row,
            "record": record,
        }
        with self._lock:
            self.entries[(strategy, prompt_id)] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def rows_for(self, strategy: str) -> list[dict]:
        return [
            entry["row"]
            for key, entry in self.entries.items()
            if key[0] == strategy and entry.get("row")
        ]


# ---------------------------------------------------------------------------
# estimate


def estimate(prompts: list[dict], strategies: list[str], max_tokens: int,
             baseline_verify: bool, search_cost: float,
             pending: set[tuple[str, str]] | None = None) -> dict:
    # Only rows that are not already cached cost anything; without a cache every
    # (prompt, strategy) pair is a run.
    runs = [
        (prompt, strategy)
        for prompt in prompts
        for strategy in strategies
        if pending is None or (strategy, prompt["id"]) in pending
    ]

    nano_in, nano_out = _price("nano")
    ultra_in, ultra_out = _price("ultra")
    verify_est = (
        EST_EXTRACTOR_IN * nano_in
        + EST_EXTRACTOR_OUT * nano_out
        + EST_JUDGE_IN * ultra_in
        + EST_JUDGE_OUT * ultra_out
    ) / 1_000_000 + EST_SEARCHES_PER_VERIFY * search_cost
    grade_est = (EST_GRADER_IN * ultra_in + EST_GRADER_OUT * ultra_out) / 1_000_000

    answer_cost = 0.0
    for prompt, strategy in runs:
        tokens_in = len(prompt["prompt"]) / 4
        # The routed tier is unknown before routing; price TrustRouter as super.
        price_in, price_out = _price("super" if strategy == "trustrouter" else strategy)
        answer_cost += (
            tokens_in * price_in + min(max_tokens, TYPICAL_ANSWER_TOKENS) * price_out
        ) / 1_000_000

    baseline_verifies = (
        sum(1 for _, strategy in runs if strategy != "trustrouter") if baseline_verify else 0
    )
    tr_runs = sum(1 for _, strategy in runs if strategy == "trustrouter")
    tr_verifies = tr_runs * (1 + EST_REPAIR_RATE)
    tr_repairs = tr_runs * EST_REPAIR_RATE
    repair_cost = tr_repairs * (600 * ultra_in + 500 * ultra_out) / 1_000_000

    verify_total = (baseline_verifies + tr_verifies) * verify_est
    grade_calls = sum(1 for prompt, _ in runs if prompt["reference"])
    grade_total = grade_calls * grade_est
    worst = answer_cost * 3 + verify_total * 1.6 + grade_total * 1.6 + repair_cost

    return {
        "answer_calls": len(runs),
        "grade_calls": grade_calls,
        "baseline_verifies": int(baseline_verifies),
        "tr_verifies": tr_verifies,
        "searches": (baseline_verifies + tr_verifies) * EST_SEARCHES_PER_VERIFY,
        "answer_cost": answer_cost,
        "verify_cost": verify_total,
        "verify_each": verify_est,
        "grade_cost": grade_total,
        "repair_cost": repair_cost,
        "typical": answer_cost + verify_total + grade_total + repair_cost,
        "worst": worst,
    }


def print_plan(prompts: list[dict], strategies: list[str], plan: dict, max_usd: float,
               out_dir: Path, cached_rows: int = 0) -> None:
    graded = sum(1 for p in prompts if p["reference"])
    counts = Counter(p["category"] for p in prompts)
    print(f"Prompts:   {len(prompts)} ({graded} graded, {len(prompts) - graded} ungraded)")
    print("Topics:    " + ", ".join(f"{name} {n}" for name, n in counts.items()))
    print(f"Strategies: {', '.join(strategies)}")
    pairs = len(prompts) * len(strategies)
    print(f"Rows:      {pairs - cached_rows} to run, {cached_rows} already cached (free)")
    print(
        f"Calls:     {plan['answer_calls']} answers, {plan['grade_calls']} grades, "
        f"{plan['baseline_verifies'] + plan['tr_verifies']:.0f} verifier runs "
        f"(each = 1 extractor + 1 judge + ~{EST_SEARCHES_PER_VERIFY} searches, "
        f"~${plan['verify_each']:.4f})"
    )
    print(
        f"Estimate:  ~${plan['typical']:.4f} typical, ~${plan['worst']:.4f} worst case"
    )
    print(
        f"             answers ${plan['answer_cost']:.4f} + verify ${plan['verify_cost']:.4f}"
        f" + grade ${plan['grade_cost']:.4f} + repair ${plan['repair_cost']:.4f}"
    )
    print(f"Cap:       ${max_usd:.2f}   outputs -> {out_dir}")
    print(f"           checked before each call against its estimated cost")
    if plan["typical"] > max_usd:
        prompts_affordable = max(1, int(max_usd / max(plan["typical"] / max(1, len(prompts)), 1e-9)))
        print(
            f"  ! the estimate is above the cap: expect to stop after roughly "
            f"{min(prompts_affordable, len(prompts))} of {len(prompts)} prompts"
        )


def confirm(max_usd: float, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        answer = input("\nType y to spend real credits and start: ").strip().lower()
    except EOFError:
        print("\nNo terminal to confirm on; nothing was called. Use --yes to skip this.")
        return False
    return answer in {"y", "yes"}


# ---------------------------------------------------------------------------
# grading


def _parse_json_object(text: str) -> dict | None:
    """First complete JSON object in model output; tolerates fences and prose."""
    cleaned = re.sub(r"```(?:json)?", "", _strip_think(text or ""))
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    return parsed if isinstance(parsed, dict) else None
        start = cleaned.find("{", start + 1)
    return None


def grade(question: str, reference: str, answer: str, budget: Budget) -> tuple[bool | None, str, float]:
    """Return (correct, reason, cost). Cost is reported, never charged to a strategy."""
    budget.guard("grade")
    messages = build_prompt("grader", question=question, reference=reference, answer=answer)
    try:
        result = gateway_llm.call_llm(
            GRADER_TIER, messages, max_tokens=GRADER_MAX_TOKENS, temperature=GRADER_TEMPERATURE
        )
    except Exception as exc:  # noqa: BLE001 - a failed grade must not lose the row
        return None, f"grader call failed: {type(exc).__name__}: {exc}", 0.0
    budget.charge(result.cost_usd)

    parsed = _parse_json_object(result.text)
    if parsed is None:
        match = re.search(r'"correct"\s*:\s*(true|false)', result.text or "", re.IGNORECASE)
        if not match:
            return None, "grader output unparseable", result.cost_usd
        parsed = {"correct": match.group(1).lower() == "true", "reason": "parsed from prose"}

    value = parsed.get("correct")
    if isinstance(value, str):
        value = value.strip().lower() == "true"
    if not isinstance(value, bool):
        return None, f"grader returned no boolean: {parsed.get('correct')!r}", result.cost_usd
    reason = " ".join(str(parsed.get("reason") or "").split())
    return value, reason, result.cost_usd


# ---------------------------------------------------------------------------
# strategies


def _supported_rate(claims: list[dict]) -> float | None:
    total = len(claims or [])
    if not total:
        return None
    supported = sum(1 for claim in claims if claim.get("verdict") == "supported")
    return supported / total


def _row(strategy: str, prompt: dict, **fields) -> dict:
    row = {
        "strategy": strategy,
        "prompt_id": prompt["id"],
        "category": prompt["category"],
        "answer": "",
        "correct": None,
        "supported_rate": None,
        "latency_ms": None,
        "cost_usd": 0.0,
        "model_used": "",
        "status": "error",
        "escalated": False,
        "repaired": False,
        "error": "",
        "n_claims": 0,
        "grade_reason": "",
        "verify_cost_usd": 0.0,
    }
    row.update(fields)
    return row


def run_baseline(strategy: str, prompt: dict, max_tokens: int, budget: Budget,
                 meter: VerifyMeter, baseline_verify: bool) -> tuple[dict, None]:
    budget.guard("answer")
    started = time.perf_counter()
    try:
        result = gateway_llm.call_llm(
            strategy,
            [{"role": "user", "content": prompt["prompt"]}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 - one failed row must not stop the run
        return _row(strategy, prompt, latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    error=f"{type(exc).__name__}: {exc}"), None
    budget.charge(result.cost_usd)
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    row = _row(
        strategy,
        prompt,
        answer=result.text,
        latency_ms=latency_ms,
        cost_usd=round(result.cost_usd, 8),
        model_used=result.model,
        status="ok" if result.text.strip() else "empty",
        verify_cost_usd=0.0,
    )

    if baseline_verify and result.text.strip():
        meter.reset_thread()
        budget.guard("verify")
        try:
            verified = verifier_verify(prompt["prompt"], result.text)
            claims = verified.get("claims") or []
            budget.charge(verified.get("cost_usd") or 0.0)
            row["supported_rate"] = _supported_rate(claims)
            row["n_claims"] = len(claims)
            row["verify_cost_usd"] = round(float(verified.get("cost_usd") or 0.0), 8)
        except BudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - measurement must not lose the answer
            row["error"] = f"baseline verification failed: {type(exc).__name__}: {exc}"
    return row, None


def run_trustrouter(prompt: dict, max_tokens: int, budget: Budget,
                    meter: VerifyMeter) -> tuple[dict, dict | None]:
    meter.reset_thread()
    budget.guard("answer")
    try:
        record = run_pipeline(
            [{"role": "user", "content": prompt["prompt"]}],
            mode="verified",
            max_tokens=max_tokens,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 - one failed row must not stop the run
        record = getattr(exc, "record", None)
        row = _row(
            "trustrouter",
            prompt,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            verify_cost_usd=round(meter.row_cost(), 8),
        )
        if record:
            costs = record["metrics"]["cost_usd"]
            charge_budget_for_record(budget, costs)
            row.update(
                answer=(record.get("answer") or {}).get("text") or "",
                latency_ms=record["metrics"]["total_ms"],
                cost_usd=round(float(costs["total"]), 8),
                model_used=record["route"]["model_id"],
            )
        return row, record

    costs = record["metrics"]["cost_usd"]
    # Verification is billed by the meter (at its measured cost); charge the
    # record's non-verification lines here so nothing is counted twice.
    charge_budget_for_record(budget, costs)

    claims = record.get("claims") or []
    row = _row(
        "trustrouter",
        prompt,
        answer=(record.get("answer") or {}).get("text") or "",
        supported_rate=_supported_rate(claims),
        latency_ms=record["metrics"]["total_ms"],
        cost_usd=round(float(costs["total"]), 8),
        model_used=record["route"]["model_id"],
        status="ok" if record["status"] in {"verified", "repaired"} else record["status"],
        escalated=bool(record["route"].get("upgraded_for_confidence")),
        repaired=record.get("repair") is not None,
        error=record.get("error") or "",
        n_claims=len(claims),
        verify_cost_usd=round(meter.row_cost(), 8),
    )
    return row, record


def charge_budget_for_record(budget: Budget, costs: dict) -> None:
    budget.charge(float(costs.get("total", 0.0)) - float(costs.get("verification", 0.0)))


BUDGET_GRADE_NOTE = "not graded: spend limit reached"


def _grade_row(prompt: dict, row: dict, budget: Budget) -> bool:
    """Grade in place. Returns True if a spend stop cancelled the grade."""
    try:
        correct, reason, _ = grade(
            prompt["prompt"], prompt["reference"], row["answer"], budget
        )
    except BudgetExceeded:
        row["correct"] = None
        row["grade_reason"] = BUDGET_GRADE_NOTE
        return True
    row["correct"] = correct
    row["grade_reason"] = reason
    return False


def run_prompt(prompt: dict, strategies: list[str], args, budget: Budget,
               meter: VerifyMeter, cache: Cache) -> list[dict]:
    rows: list[dict] = []
    sha = prompt_sha(prompt["prompt"])
    for strategy in strategies:
        # Cached rows are free, so they are read even after the cap is hit.
        cached = cache.get(strategy, prompt["id"], sha)
        if cached:
            row = dict(cached["row"])
            record = cached.get("record")
            # A row left ungraded by a spend stop gets its grade on a later run;
            # grading is the only part of a cached row that is worth retrying.
            if (
                prompt["reference"]
                and row["status"] == "ok"
                and row["correct"] is None
                and row.get("grade_reason") == BUDGET_GRADE_NOTE
            ):
                if not _grade_row(prompt, row, budget):
                    cache.put(strategy, prompt["id"], sha, row, record)
            rows.append(row)
            continue

        if budget.blocked():
            break

        meter.reset_thread()
        try:
            if strategy == "trustrouter":
                row, record = run_trustrouter(prompt, args.max_tokens, budget, meter)
            else:
                row, record = run_baseline(
                    strategy, prompt, args.max_tokens, budget, meter, not args.no_baseline_verify
                )
        except BudgetExceeded:
            break

        stopped = False
        if prompt["reference"] and row["status"] == "ok":
            stopped = _grade_row(prompt, row, budget)
        elif prompt["reference"]:
            row["grade_reason"] = f"not graded: status {row['status']}"

        rows.append(row)
        cache.put(strategy, prompt["id"], sha, row, record)
        print(
            f"  {prompt['id']:<6} {strategy:<11} {row['status']:<10} "
            f"{_fmt_correct(row['correct']):<7} ${row['cost_usd']:.5f} "
            f"{row['latency_ms'] or 0:>7.0f} ms  claims={row['n_claims']}",
            flush=True,
        )
        if stopped:
            break
    return rows


def _fmt_correct(value) -> str:
    return "-" if value is None else ("ok" if value else "wrong")


# ---------------------------------------------------------------------------
# reporting


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(p / 100.0 * len(ordered)) - 1))
    return ordered[index]


def summarise(rows: list[dict], strategies: list[str],
              prompts: dict[str, dict]) -> tuple[list[dict], list[str], dict]:
    """Aggregate over prompts every strategy completed. Returns (rows, common_ids, extras).

    A prompt is only comparable when every strategy produced an answer and, for a
    prompt with a reference, every answer also has a grade. Otherwise one
    strategy would be scored on a different set of answers than another.
    """
    by_prompt: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_prompt.setdefault(row["prompt_id"], {})[row["strategy"]] = row

    order: dict[str, int] = {}
    for position, row in enumerate(rows):
        order.setdefault(row["prompt_id"], position)

    def complete(prompt_id: str) -> bool:
        per_strategy = by_prompt[prompt_id]
        graded = bool(prompts.get(prompt_id, {}).get("reference"))
        for strategy in strategies:
            row = per_strategy.get(strategy)
            if not row or row.get("status") != "ok":
                return False
            if graded and row.get("correct") is None:
                return False
        return True

    common = [prompt_id for prompt_id in by_prompt if complete(prompt_id)]
    common.sort(key=lambda pid: order.get(pid, 0))

    summaries: list[dict] = []
    ultra_total = None
    for strategy in strategies:
        subset = [by_prompt[pid][strategy] for pid in common]
        graded = [r for r in subset if r["correct"] is not None]
        rates = [r["supported_rate"] for r in subset if r["supported_rate"] is not None]
        latencies = [r["latency_ms"] for r in subset if r["latency_ms"] is not None]
        total_cost = sum(r["cost_usd"] for r in subset)
        if strategy == "ultra":
            ultra_total = total_cost
        summaries.append(
            {
                "strategy": strategy,
                "n": len(subset),
                "n_graded": len(graded),
                "accuracy": (sum(1 for r in graded if r["correct"]) / len(graded)) if graded else None,
                "supported_claims_rate": (sum(rates) / len(rates)) if rates else None,
                "n_supported": len(rates),
                "avg_cost_usd": (total_cost / len(subset)) if subset else None,
                "total_cost_usd": total_cost,
                "cost_vs_ultra_pct": None,  # filled below
                "p50_latency_ms": _percentile(latencies, 50),
                "p95_latency_ms": _percentile(latencies, 95),
                "repair_rate": (sum(1 for r in subset if r["repaired"]) / len(subset)) if subset else None,
                "escalation_rate": (sum(1 for r in subset if r["escalated"]) / len(subset)) if subset else None,
            }
        )

    if ultra_total:
        for item in summaries:
            item["cost_vs_ultra_pct"] = 100.0 * item["total_cost_usd"] / ultra_total

    skipped = sorted(
        {pid for pid, per in by_prompt.items() if pid not in common}
    )
    extras = {"skipped": skipped, "by_prompt": by_prompt}
    return summaries, common, extras


def _pct(value) -> str:
    return "n/a" if value is None else f"{100.0 * value:.1f}%"


def print_summary(summaries: list[dict], common: list[str], extras: dict,
                  strategies: list[str], rows: list[dict]) -> None:
    print("\n=== summary (prompts completed by every strategy) ===")
    print(f"common prompts: {len(common)}   excluded: {len(extras['skipped'])}")
    if extras["skipped"]:
        preview = ", ".join(extras["skipped"][:8])
        more = "" if len(extras["skipped"]) <= 8 else f" (+{len(extras['skipped']) - 8} more)"
        print(f"  excluded ids: {preview}{more}")
    if not common:
        print(
            "  ! no prompt was completed by every strategy, so there is nothing to compare.\n"
            "    Every prompt needs an answered row for each strategy (and a grade where the\n"
            "    prompt has a reference). Raise --max-usd or rerun and cached rows are reused."
        )

    header = (
        f"{'strategy':<12} {'n':>3} {'accuracy':>9} {'supported (rows)':>20} {'avg$':>9} "
        f"{'total$':>9} {'vs ultra':>9} {'p50 ms':>8} {'p95 ms':>8} {'repair':>7} {'escal':>6}"
    )
    print(header)
    print("-" * len(header))
    for item in summaries:
        vs_ultra = "n/a" if item["cost_vs_ultra_pct"] is None else f"{item['cost_vs_ultra_pct']:.0f}%"
        supported = _pct(item["supported_claims_rate"])
        if item["n_supported"]:
            supported = f"{supported} ({item['n_supported']}/{item['n']})"
        print(
            f"{item['strategy']:<12} {item['n']:>3} {_pct(item['accuracy']):>9} "
            f"{supported:>20} "
            f"{('$%.4f' % item['avg_cost_usd']) if item['avg_cost_usd'] is not None else 'n/a':>9} "
            f"{'$%.4f' % item['total_cost_usd']:>9} {vs_ultra:>9} "
            f"{(item['p50_latency_ms'] or 0):>8.0f} {(item['p95_latency_ms'] or 0):>8.0f} "
            f"{_pct(item['repair_rate']):>7} {_pct(item['escalation_rate']):>6}"
        )

    by_prompt = extras["by_prompt"]
    if common:
        unchecked = ", ".join(
            f"{strategy} {sum(1 for pid in common if not by_prompt[pid][strategy].get('n_claims'))}/{len(common)}"
            for strategy in strategies
        )
        print(
            "  answers the verifier could not turn into claims (nothing was checked): "
            + unchecked
        )

    print("\n=== by category (same common set) ===")
    header = f"{'category':<14} {'strategy':<12} {'n':>3} {'accuracy':>9} {'supported':>10} {'avg$':>9}"
    print(header)
    print("-" * len(header))
    categories: list[str] = []
    for prompt_id in common:
        category = by_prompt[prompt_id][strategies[0]]["category"]
        if category not in categories:
            categories.append(category)
    for category in categories:
        for strategy in strategies:
            subset = [
                by_prompt[pid][strategy]
                for pid in common
                if by_prompt[pid][strategy]["category"] == category
            ]
            graded = [r for r in subset if r["correct"] is not None]
            rates = [r["supported_rate"] for r in subset if r["supported_rate"] is not None]
            cost = sum(r["cost_usd"] for r in subset)
            accuracy = (sum(1 for r in graded if r["correct"]) / len(graded)) if graded else None
            supported = (sum(rates) / len(rates)) if rates else None
            print(
                f"{category:<14} {strategy:<12} {len(subset):>3} {_pct(accuracy):>9} "
                f"{_pct(supported):>10} ${cost / len(subset):.4f}" if subset else ""
            )

    print("\n=== all-in cost (record cost + measured verification) ===")
    totals: dict[str, float] = {}
    for strategy in strategies:
        totals[strategy] = sum(
            by_prompt[pid][strategy]["cost_usd"]
            + (by_prompt[pid][strategy].get("verify_cost_usd") or 0.0)
            for pid in common
        )
    base = totals.get("ultra")
    header = f"{'strategy':<12} {'avg$':>9} {'total$':>9} {'vs ultra':>9}"
    print(header)
    print("-" * len(header))
    for strategy in strategies:
        total = totals[strategy]
        vs = f"{100.0 * total / base:.0f}%" if base else "n/a"
        print(
            f"{strategy:<12} ${total / max(1, len(common)):>7.4f} ${total:>8.4f} {vs:>9}"
        )
    print("  (what each strategy costs to deliver an equally checked answer)")

    verify_spend = sum(r.get("verify_cost_usd") or 0.0 for r in rows)
    record_verify = sum(
        (r.get("cost_usd") or 0.0) for r in rows if r["strategy"] == "trustrouter"
    )
    print(
        f"\nmeasured verification spend across all rows: ${verify_spend:.4f} "
        "(charged to --max-usd, not to any strategy)"
    )
    print(
        "note: rows.csv cost_usd for trustrouter is the record's base+verification+repair, and "
        "the record charges TRUSTROUTER_VERIFY_COST_USD (currently "
        f"{_env_verify_cost():.4f}) per verification. Set that env var to the measured "
        f"average (~${verify_spend / max(1, len(rows)):.4f} per row) to price verification "
        "into the record itself."
    )
    print(f"record cost total for trustrouter rows: ${record_verify:.4f} (verification excluded)")


def _env_verify_cost() -> float:
    try:
        return float(os.getenv("TRUSTROUTER_VERIFY_COST_USD", "0") or 0.0)
    except ValueError:
        return 0.0


def write_rows_csv(rows: list[dict], path: Path) -> None:
    fields = [
        "strategy", "prompt_id", "category", "answer", "correct", "supported_rate",
        "latency_ms", "cost_usd", "verify_cost_usd", "model_used", "status", "escalated",
        "repaired", "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "strategy": row["strategy"],
                    "prompt_id": row["prompt_id"],
                    "category": row["category"],
                    "answer": row["answer"],
                    "correct": "" if row["correct"] is None else str(bool(row["correct"])).lower(),
                    "supported_rate": "" if row["supported_rate"] is None else f"{row['supported_rate']:.4f}",
                    "latency_ms": row["latency_ms"] if row["latency_ms"] is not None else "",
                    "cost_usd": f"{row['cost_usd']:.8f}",
                    "verify_cost_usd": f"{row.get('verify_cost_usd') or 0.0:.8f}",
                    "model_used": row["model_used"],
                    "status": row["status"],
                    "escalated": str(bool(row["escalated"])).lower(),
                    "repaired": str(bool(row["repaired"])).lower(),
                    "error": row.get("error") or "",
                }
            )


def write_summary_csv(summaries: list[dict], path: Path) -> None:
    fields = [
        "strategy", "n", "accuracy", "supported_claims_rate", "avg_cost_usd",
        "total_cost_usd", "cost_vs_ultra_pct", "p50_latency_ms", "p95_latency_ms",
        "repair_rate", "escalation_rate",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in summaries:
            writer.writerow(
                {
                    "strategy": item["strategy"],
                    "n": item["n"],
                    "accuracy": "" if item["accuracy"] is None else f"{item['accuracy']:.4f}",
                    "supported_claims_rate": "" if item["supported_claims_rate"] is None else f"{item['supported_claims_rate']:.4f}",
                    "avg_cost_usd": "" if item["avg_cost_usd"] is None else f"{item['avg_cost_usd']:.8f}",
                    "total_cost_usd": f"{item['total_cost_usd']:.8f}",
                    "cost_vs_ultra_pct": "" if item["cost_vs_ultra_pct"] is None else f"{item['cost_vs_ultra_pct']:.2f}",
                    "p50_latency_ms": "" if item["p50_latency_ms"] is None else f"{item['p50_latency_ms']:.2f}",
                    "p95_latency_ms": "" if item["p95_latency_ms"] is None else f"{item['p95_latency_ms']:.2f}",
                    "repair_rate": "" if item["repair_rate"] is None else f"{item['repair_rate']:.4f}",
                    "escalation_rate": "" if item["escalation_rate"] is None else f"{item['escalation_rate']:.4f}",
                }
            )


def write_failures_csv(rows: list[dict], prompts: dict[str, dict], records: dict[str, dict],
                       path: Path) -> int:
    failures = [r for r in rows if r["strategy"] == "trustrouter" and r["correct"] is False]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["prompt_id", "category", "prompt", "reference", "answer",
                        "claims", "reason"],
        )
        writer.writeheader()
        for row in failures:
            record = records.get(row["prompt_id"]) or {}
            claims = [
                {
                    "text": claim.get("text"),
                    "verdict": claim.get("verdict"),
                    "source_url": claim.get("source_url"),
                }
                for claim in record.get("claims") or []
            ]
            writer.writerow(
                {
                    "prompt_id": row["prompt_id"],
                    "category": row["category"],
                    "prompt": prompts[row["prompt_id"]]["prompt"],
                    "reference": prompts[row["prompt_id"]]["reference"],
                    "answer": row["answer"],
                    "claims": json.dumps(claims, ensure_ascii=False),
                    "reason": row.get("grade_reason") or "",
                }
            )
    return len(failures)


# ---------------------------------------------------------------------------
# main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark nano/super/ultra against TrustRouter.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--prompts", default=str(REPO / "benchmark" / "prompts.jsonl"))
    parser.add_argument("--limit", type=int, default=None, help="use only the first N prompts")
    parser.add_argument(
        "--categories",
        default=None,
        help="comma-separated categories to keep, e.g. factual_hard,fake_entity",
    )
    parser.add_argument("--strategies", default="nano,super,ultra,trustrouter")
    parser.add_argument("--max-usd", type=float, default=3.0)
    parser.add_argument("--out", default=str(REPO / "results"))
    parser.add_argument("--no-baseline-verify", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    strategies = [s.strip().lower() for s in args.strategies.split(",") if s.strip()]
    unknown = [s for s in strategies if s not in STRATEGIES]
    if unknown:
        raise SystemExit(f"unknown strategies {unknown}; pick from {', '.join(STRATEGIES)}")
    strategies = [s for s in STRATEGIES if s in strategies]  # stable, canonical order
    if not strategies:
        raise SystemExit("no strategies selected")
    if args.max_usd <= 0:
        raise SystemExit("--max-usd must be positive")
    if args.max_tokens < 100:
        raise SystemExit("--max-tokens below 100 returns nothing from a reasoning model")

    categories = None
    if args.categories:
        categories = [c.strip() for c in args.categories.split(",") if c.strip()]
        if not categories:
            raise SystemExit("--categories was empty")

    prompts = load_prompts(
        Path(args.prompts).expanduser().resolve(), args.limit, categories
    )
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Keep benchmark decisions out of the demo DB.
    os.environ["TRUSTROUTER_DB"] = str(out_dir / "decisions.db")

    # Cached rows are reused for free, so the estimate only counts what will run.
    cache = Cache(out_dir / "cache.jsonl")
    pending = {
        (strategy, prompt["id"])
        for prompt in prompts
        for strategy in strategies
        if cache.get(strategy, prompt["id"], prompt_sha(prompt["prompt"])) is None
    }
    plan = estimate(prompts, strategies, args.max_tokens, not args.no_baseline_verify,
                    _search_cost(), pending=pending)
    print_plan(prompts, strategies, plan, args.max_usd, out_dir,
               cached_rows=len(prompts) * len(strategies) - len(pending))

    if gateway_config.mock_llm():
        raise SystemExit("\nTRUSTROUTER_MOCK_LLM=1: the benchmark needs real models.")
    try:
        gateway_config.api_key()
        gateway_config.model_for_tier("nano")
        gateway_config.model_for_tier("ultra")
    except gateway_config.ConfigError as exc:
        raise SystemExit(f"\n{exc}")
    if not os.getenv("TAVILY_API_KEY", "").strip():
        print("\n  ! TAVILY_API_KEY is missing: every verification would return no claims.")

    if not confirm(args.max_usd, args.yes):
        print("No calls were made.")
        return 0

    gateway_db.init_db()
    reserves = {
        "answer": plan["answer_cost"] / max(1, plan["answer_calls"]),
        "verify": plan["verify_each"],
        "grade": plan["grade_cost"] / max(1, plan["grade_calls"]),
    }
    budget = Budget(args.max_usd, reserves)
    meter = VerifyMeter(budget)
    gateway_verify.set_verifier(meter)  # the pipeline now uses the real verifier

    all_rows: dict[tuple[str, str], dict] = {}
    records: dict[str, dict] = {}
    for entry in cache.entries.values():
        row = entry.get("row") or {}
        if row:
            all_rows[(row["strategy"], row["prompt_id"])] = row
        if entry.get("record") and entry["strategy"] == "trustrouter":
            records[row.get("prompt_id", "")] = entry["record"]

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(prompts))) as pool:
        futures = {
            pool.submit(run_prompt, prompt, strategies, args, budget, meter, cache): prompt
            for prompt in prompts
        }
        for future in as_completed(futures):
            prompt = futures[future]
            try:
                rows = future.result()
            except Exception as exc:  # noqa: BLE001 - one prompt must not kill the run
                print(f"  {prompt['id']}: runner failed: {type(exc).__name__}: {exc}")
                continue
            for row in rows:
                all_rows[(row["strategy"], row["prompt_id"])] = row
                if row["strategy"] == "trustrouter":
                    entry = cache.get("trustrouter", row["prompt_id"], prompt_sha(prompt["prompt"]))
                    if entry and entry.get("record"):
                        records[row["prompt_id"]] = entry["record"]

    elapsed = time.perf_counter() - started
    ordered_rows = [
        all_rows[(strategy, prompt["id"])]
        for prompt in prompts
        for strategy in strategies
        if (strategy, prompt["id"]) in all_rows
    ]

    summaries, common, extras = summarise(
        ordered_rows, strategies, {p["id"]: p for p in prompts}
    )
    write_rows_csv(ordered_rows, out_dir / "rows.csv")
    write_summary_csv(summaries, out_dir / "summary.csv")
    failures = write_failures_csv(
        ordered_rows, {p["id"]: p for p in prompts}, records, out_dir / "failures.csv"
    )
    with (out_dir / "records.jsonl").open("w", encoding="utf-8") as handle:
        for prompt in prompts:
            record = records.get(prompt["id"])
            if record:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print_summary(summaries, common, extras, strategies, ordered_rows)
    print(
        f"\nrun: {len(ordered_rows)} rows in {elapsed:.1f}s, spent ${budget.spent:.4f} "
        f"of ${args.max_usd:.2f}"
        + (" (stopped at the cap)" if budget.reached else "")
    )
    print(f"failures: {failures} trustrouter row(s) graded incorrect")
    print(f"wrote {out_dir / 'rows.csv'}, summary.csv, failures.csv, records.jsonl, cache.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
