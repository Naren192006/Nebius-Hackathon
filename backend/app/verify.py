"""Adapter for Backend B's verifier.

Contract: ``verify(question, answer) -> list[claims]``. Each claim should look
like ``{"text": str, "verdict": "supported|unsupported|unclear",
"source_url": str|null, "evidence_snippet": str|null}``. Boolean
``supported`` values and plain strings are accepted for compatibility; plain
strings are treated as ``unclear`` because they carry no verdict.

Until B lands, the default verifier is a stub returning ``[]``. A stub is
treated as *unavailable*, so decisions end up ``unchecked`` instead of falsely
``verified``. Point the gateway at the real verifier with:

    TRUSTROUTER_VERIFIER=backend_b.verifier:verify
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import time
from typing import Any, Callable

logger = logging.getLogger("trustrouter.verify")

Verifier = Callable[[str, str], list[Any]]

MAX_CLAIMS = 50
VERDICTS = {"supported", "unsupported", "unclear"}

_override: Verifier | None = None

_THINK_BLOCK = re.compile(r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?\s*>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think(?:ing)?\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)

_SUPPORTED_ALIASES = {"supported", "verified", "true", "pass", "passed", "yes", "1"}
_UNSUPPORTED_ALIASES = {"unsupported", "refuted", "false", "fail", "failed", "no", "0", "contradicted"}
_UNCLEAR_ALIASES = {"unclear", "unverified", "unknown", "uncertain", "partial", "needs review"}


def stub_verify(question: str, answer: str) -> list[dict[str, Any]]:
    """Placeholder verifier: claims nothing and is marked unavailable."""
    return []


def _load_target(target: str) -> Verifier:
    module_name, _, attr = target.partition(":")
    if not module_name or not attr:
        raise ValueError(f"TRUSTROUTER_VERIFIER must be 'module:function', got {target!r}")
    module = importlib.import_module(module_name)
    candidate = getattr(module, attr)
    if not callable(candidate):
        raise TypeError(f"{target} is not callable")
    return candidate


def set_verifier(verifier: Verifier | None) -> None:
    """Test/demo hook to install a verifier without touching the environment."""
    global _override
    _override = verifier


def get_verifier() -> Verifier:
    if _override is not None:
        return _override
    target = os.environ.get("TRUSTROUTER_VERIFIER", "").strip()
    if target:
        return _load_target(target)
    return stub_verify


def verifier_name() -> str:
    if _override is not None:
        return getattr(_override, "__name__", "custom")
    target = os.environ.get("TRUSTROUTER_VERIFIER", "").strip()
    return target or "stub"


def availability() -> tuple[bool, str, str | None]:
    """Return (usable, name, reason).

    A stub or a broken import means the answer cannot be checked, which maps to
    the gateway's ``unchecked`` status rather than ``verified``.
    """
    if _override is not None:
        if _override is stub_verify:
            return False, "stub", "no verifier configured; the stub returns no claims"
        return True, verifier_name(), None
    target = os.environ.get("TRUSTROUTER_VERIFIER", "").strip()
    if not target:
        return False, "stub", "no verifier configured; the stub returns no claims"
    try:
        _load_target(target)
    except Exception as exc:  # noqa: BLE001 - surfaced as verification.error
        logger.warning("could not load verifier %s: %s", target, exc)
        return False, target, f"{type(exc).__name__}: {exc}"
    return True, target, None


def is_available() -> bool:
    return availability()[0]


def _normalize_verdict(value: Any) -> str:
    if isinstance(value, bool):
        return "supported" if value else "unsupported"
    if isinstance(value, (int, float)):
        return "supported" if value else "unsupported"
    if value is None:
        return "unclear"
    text = str(value).strip().lower()
    if text in _SUPPORTED_ALIASES:
        return "supported"
    if text in _UNSUPPORTED_ALIASES:
        return "unsupported"
    if text in _UNCLEAR_ALIASES:
        return "unclear"
    return "unclear"


def _verdict_from_item(item: dict[str, Any]) -> str:
    if "verdict" in item:
        return _normalize_verdict(item["verdict"])
    if "supported" in item:
        return _normalize_verdict(item["supported"])
    if "status" in item:
        return _normalize_verdict(item["status"])
    return "unclear"


def _source_url(item: dict[str, Any]) -> str | None:
    for key in ("source_url", "url", "source"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            if candidate.startswith(("http://", "https://")):
                return candidate
    return None


def _evidence(item: dict[str, Any]) -> str | None:
    for key in ("evidence_snippet", "snippet", "quote", "evidence"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _strip_reasoning(text: str) -> str:
    """Drop <think>/<thinking> blocks, closed or not, before parsing."""
    cleaned = _THINK_BLOCK.sub("", text)
    return _THINK_OPEN.sub("", cleaned)


def extract_json_array(text: str) -> list[Any] | None:
    """Return the first complete JSON array in model output, or None.

    Tolerates reasoning blocks, markdown fences, and prose before or after the
    array, which is how models usually wrap structured output.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = re.sub(r"```(?:json)?", "", _strip_reasoning(text))
    start = cleaned.find("[")
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
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    return parsed if isinstance(parsed, list) else None
        start = cleaned.find("[", start + 1)
    return None


def normalize_claims(raw: Any) -> list[dict[str, Any]]:
    """Normalize B's claim objects into the v1.1 claim shape.

    Accepts a list of claim dicts, a ``{"claims": [...]}`` wrapper, a bare
    string, or a string containing a JSON array (models often return that).
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parsed = extract_json_array(raw)
        if parsed is not None:
            raw = parsed
    if isinstance(raw, dict):
        raw = raw.get("claims", [])
        if not isinstance(raw, list):
            raw = [raw]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise TypeError(f"verifier must return a list of claims, got {type(raw).__name__}")

    claims: list[dict[str, Any]] = []
    for item in raw[:MAX_CLAIMS]:
        if isinstance(item, str):
            text = item.strip()
            if text:
                claims.append(
                    {
                        "text": text,
                        "verdict": "unclear",
                        "source_url": None,
                        "evidence_snippet": None,
                    }
                )
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("claim") or item.get("statement") or ""
        text = str(text).strip()
        if not text:
            continue
        claims.append(
            {
                "text": text,
                "verdict": _verdict_from_item(item),
                "source_url": _source_url(item),
                "evidence_snippet": _evidence(item),
            }
        )
    return claims


def run_verification(
    question: str, answer: str, verifier: Verifier | None = None
) -> tuple[list[dict[str, Any]], float, str]:
    """Call the verifier and return (normalized claims, elapsed_ms, name)."""
    fn = verifier or get_verifier()
    started = time.perf_counter()
    raw = fn(question, answer)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return normalize_claims(raw), elapsed_ms, verifier_name()


def verify(question: str, answer: str) -> list[dict[str, Any]]:
    """Backend B-compatible entry point returning normalized claims."""
    claims, _, _ = run_verification(question, answer)
    return claims
