"""Claim-level verification: extract -> search all claims -> judge once.

`verify(question, answer)` returns
    {"claims": [{"text", "verdict", "source_url", "evidence_snippet", "reason"}],
     "cost_usd": float, "searches": int}

verdict is always one of supported / unsupported / unclear. Anything the judge
does not settle (unparseable output, missing item, missing evidence) becomes
"unclear" - absence of evidence is never treated as support.

When extraction yields no claims at all, the call is unverified rather than
clean. The raw extractor output and the answer go to
logs/extractor_failures.jsonl (override with TRUSTROUTER_EXTRACTOR_LOG) so the
cause is visible later; the question is never written there.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
_ENV = BACKEND / ".env"
if _ENV.exists():
    load_dotenv(_ENV)
else:
    load_dotenv()

for candidate in (str(HERE), str(BACKEND)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from llm import call_llm  # noqa: E402
from prompts.load import build, claims_block  # noqa: E402
from search import search_evidence, tavily_configured  # noqa: E402

VERDICTS = ("supported", "unsupported", "unclear")
DEFAULT_SEARCH_COST = 0.008

# When extraction yields nothing we keep the raw model text here, next to the
# gateway's own log. It records the answer and the extractor output only: no
# API keys, and never the question, which may be private.
EXTRACTOR_LOG_ENV = "TRUSTROUTER_EXTRACTOR_LOG"
EXTRACTOR_LOG_PATH = HERE.parents[1] / "backend" / "logs" / "extractor_failures.jsonl"
MAX_LOGGED_CHARS = 4000
_log_lock = threading.Lock()
_log_warning_shown = False
MAX_CLAIM_CHARS = 400

_THINK_BLOCK = re.compile(r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?\s*>", re.I | re.S)
_THINK_OPEN = re.compile(r"<think(?:ing)?\b[^>]*>.*\Z", re.I | re.S)


def _strip_think(text: str) -> str:
    cleaned = _THINK_BLOCK.sub("", text or "")
    return _THINK_OPEN.sub("", cleaned)


def _first_json_array(text: str):
    """Parse the first complete JSON array in a model reply, or return None."""
    cleaned = _strip_think(text)
    cleaned = re.sub(r"```(?:json)?", "", cleaned)
    start = cleaned.find("[")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("[", start + 1)
    return None


def _clean_claims(raw) -> list[str]:
    claims: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        text = " ".join(str(item).split()) if isinstance(item, str) else ""
        if not text or not text[0].isalpha() or len(text) > MAX_CLAIM_CHARS:
            continue
        key = text.casefold().rstrip(".")
        if key in seen:
            continue
        seen.add(key)
        claims.append(text)
    return claims


def _normalize_verdict(value) -> str:
    verdict = str(value or "").strip().lower()
    return verdict if verdict in VERDICTS else "unclear"


def _search_cost() -> float:
    try:
        return float(os.getenv("TAVILY_COST_PER_SEARCH", DEFAULT_SEARCH_COST))
    except ValueError:
        return DEFAULT_SEARCH_COST


def extractor_log_path() -> Path:
    """Where extraction failures go; override with TRUSTROUTER_EXTRACTOR_LOG."""
    override = os.getenv(EXTRACTOR_LOG_ENV, "").strip()
    return Path(override).expanduser() if override else EXTRACTOR_LOG_PATH


def log_extractor_failure(*, answer: str, raw: str, reason: str, parsed=None) -> None:
    """Append one JSON line about an extraction that produced no claims.

    Best-effort: a failure to log must never break verification, so an OSError
    prints one warning and is otherwise ignored.
    """
    global _log_warning_shown
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "reason": reason,
        "answer_chars": len(answer),
        "raw_chars": len(raw),
        "parsed_items": len(parsed) if isinstance(parsed, list) else None,
        "answer": answer[:MAX_LOGGED_CHARS],
        "raw": raw[:MAX_LOGGED_CHARS],
    }
    path = extractor_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _log_lock, path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:
        if not _log_warning_shown:
            _log_warning_shown = True
            print(f"[verify] warning: could not write {path}: {exc}")


async def _gather(claims: list[str]) -> list[list[dict]]:
    return list(await asyncio.gather(*(search_evidence(claim) for claim in claims)))


def _search_all(claims: list[str]) -> list[list[dict]]:
    """Run the searches concurrently, even when called from an async app."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_gather(claims))
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_gather(claims))).result()


def _blank_result(text: str, reason: str) -> dict:
    return {
        "text": text,
        "verdict": "unclear",
        "source_url": None,
        "evidence_snippet": None,
        "reason": reason,
    }


def _apply_judgement(claims: list[str], evidence: list[list[dict]], judged) -> list[dict]:
    by_index: dict[int, dict] = {}
    for position, item in enumerate(judged if isinstance(judged, list) else []):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("i", position))
        except (TypeError, ValueError):
            index = position
        by_index.setdefault(index, item)

    results: list[dict] = []
    for index, claim in enumerate(claims):
        item = by_index.get(index)
        if item is None:
            results.append(_blank_result(claim, "The judge returned no verdict for this claim."))
            continue

        verdict = _normalize_verdict(item.get("verdict"))
        source = item.get("source")
        source_url = None
        snippet = None
        if isinstance(source, int) and 0 <= source < len(evidence[index]):
            source_url = evidence[index][source]["url"]
            snippet = evidence[index][source]["snippet"]
        reason = " ".join(str(item.get("reason") or "").split())
        if not reason:
            reason = "The judge gave no reason."
        results.append(
            {
                "text": claim,
                "verdict": verdict,
                "source_url": source_url,
                "evidence_snippet": snippet,
                "reason": reason,
            }
        )
    return results


def verify(
    question: str,
    answer: str,
    *,
    max_claims: int = 8,
    extractor_max_tokens: int = 1200,
    judge_max_tokens: int = 2500,
    temperature: float = 0.0,
) -> dict:
    """Verify the factual claims in `answer` against web evidence."""
    text = (answer or "").strip()
    if not text:
        return {"claims": [], "cost_usd": 0.0, "searches": 0}

    cost_usd = 0.0
    searches = 0

    extraction = call_llm(
        "nano",
        build("extractor", answer=text, max_claims=max_claims),
        temperature=temperature,
        max_tokens=extractor_max_tokens,
    )
    cost_usd += extraction.cost_usd
    parsed = _first_json_array(extraction.text)
    claims = _clean_claims(parsed)[:max_claims]
    if not claims:
        raw_text = extraction.text or ""
        if not raw_text.strip():
            reason = "extractor_no_output"
        elif parsed is None:
            reason = "extractor_unparseable"
        else:
            reason = "extractor_no_usable_claims"
        log_extractor_failure(answer=text, raw=raw_text, reason=reason, parsed=parsed)
        return {"claims": [], "cost_usd": round(cost_usd, 6), "searches": 0}

    evidence = _search_all(claims)
    if tavily_configured():
        searches = len(claims)
    cost_usd += searches * _search_cost()

    judging = call_llm(
        "ultra",
        build("judge", question=question, claims_block=claims_block(claims, evidence)),
        temperature=temperature,
        max_tokens=judge_max_tokens,
    )
    cost_usd += judging.cost_usd

    judged = _first_json_array(judging.text)
    if judged is None:
        truncated = getattr(judging, "finish_reason", None) == "length"
        note = (
            "The judge output hit the token limit before the array closed."
            if truncated
            else "The judge output could not be parsed."
        )
        results = [_blank_result(claim, note) for claim in claims]
    else:
        results = _apply_judgement(claims, evidence, judged)
    return {
        "claims": results,
        "cost_usd": round(cost_usd, 6),
        "searches": searches,
    }
