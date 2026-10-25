"""Cheap, deterministic prompt router.

Signals are lexical and intentionally boring: length, code/math markers,
factual-risk markers, and reasoning verbs. The router proposes a tier from a
weighted score, and escalates one tier when confidence is below 0.70.
"""

from __future__ import annotations

import re
import time
from typing import Any

from . import config

NANO_THRESHOLD = 1.0
ULTRA_THRESHOLD = 3.0
CONFIDENCE_FLOOR = 0.70

_CODE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("fenced code", r"```|`[^`\n]+`"),
    ("language", r"\b(python|javascript|typescript|java|c\+\+|rust|golang|sql|html|css|bash|react|fastapi|pytest|docker|kubernetes)\b"),
    ("keyword", r"\b(def|class|import|async|await|function|const|let|var|return|except|lambda|struct|interface|npm|pip install|git merge|unit test|regex|refactor|compile)\b"),
    ("error", r"\b(traceback|stack trace|typeerror|valueerror|syntaxerror|nullpointer|segfault|exception|bug)\b"),
    ("code punctuation", r"[{};=]{2,}|\[[^\]]*\]\s*="),
)

_MATH_PATTERNS: tuple[tuple[str, str], ...] = (
    ("math term", r"\b(integral|derivative|differentiate|equation|algebra|geometry|trigonometry|probability|statistics|matrix|eigenvalue|theorem|lemma|factorial|logarithm|polynomial|calculus|optimization|proof|prove|solve|compute the)\b"),
    ("formula", r"[∑∫√≈≤≥]|\b[a-z]\s*\^\s*\d|\b\d+\s*[*/]\s*\d+"),
)

_FACTUAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("date", r"\b(?:19|20)\d{2}\b"),
    ("authorship", r"\b(who (wrote|said|invented|discovered|painted|composed|directed|founded))\b"),
    ("citation", r"\b(cite|cited|citations?|sources?|according to|bibliography|references?)\b"),
    ("recency", r"\b(latest|current|currently|today|this year|recent|newest|up-to-date|as of)\b"),
    ("fact lookup", r"\b(when did|what year|how many|population of|capital of|price of|net worth|born|died|headquarters)\b"),
)

_REASONING_PATTERNS: tuple[tuple[str, str], ...] = (
    ("reasoning verb", r"\b(explain|why|analy[sz]e|compare|contrast|evaluate|justify|critique|reason|derive|design(?:ing)?|architect|plan|strategy|implications|recommend(?:ed|s|ation)?|trade-?offs?)\b"),
    ("process", r"\b(step by step|each step|walk me through|think through|pros and cons|root cause|how would you)\b"),
)

_COMPILED_CODE = tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in _CODE_PATTERNS)
_COMPILED_MATH = tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in _MATH_PATTERNS)
_COMPILED_FACTUAL = tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in _FACTUAL_PATTERNS)
_COMPILED_REASONING = tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in _REASONING_PATTERNS)
_NUMBERED_LIST = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)


MAX_HITS = 8


def _hits(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> list[str]:
    """Return unique `label:term` matches, capped to keep scoring bounded."""
    found: list[str] = []
    seen: set[str] = set()
    for label, pattern in patterns:
        for match in pattern.finditer(text):
            term = (match.group(0) or "").strip().lower().replace("\n", " ")[:24]
            key = f"{label}:{term}" if term else label
            if key in seen:
                continue
            seen.add(key)
            found.append(key)
            if len(found) >= MAX_HITS:
                return found
    return found


def _length_score(char_len: int, has_signals: bool) -> float:
    if char_len >= 2000:
        return 2.0
    if char_len >= 800:
        return 1.5
    if char_len >= 300:
        return 0.75
    # Only trivial, signal-free one-liners get penalised; a short factual or
    # code question still deserves the tier its signals imply.
    if char_len <= 60 and not has_signals:
        return -0.5
    return 0.0


def _tier_for_score(score: float) -> str:
    if score >= ULTRA_THRESHOLD:
        return "ultra"
    if score >= NANO_THRESHOLD:
        return "super"
    return "nano"


def _boundary_margin(score: float) -> float:
    """Distance to the nearest tier threshold (>= 0)."""
    if score < NANO_THRESHOLD:
        return NANO_THRESHOLD - score
    if score < ULTRA_THRESHOLD:
        return min(score - NANO_THRESHOLD, ULTRA_THRESHOLD - score)
    return score - ULTRA_THRESHOLD


def _confidence(score: float, signal_count: int, strong_signal: bool) -> float:
    margin = min(_boundary_margin(score) / 1.5, 1.0)
    confidence = 0.62 + 0.18 * margin + 0.06 * min(signal_count, 3) / 3
    if strong_signal:
        confidence += 0.08
    return round(max(0.50, min(0.97, confidence)), 3)


def _describe(tier: str, features: dict[str, Any], score: float) -> str:
    parts: list[str] = []
    if features["code"]:
        parts.append("code signals (" + ", ".join(features["code_hits"][:4]) + ")")
    if features["math"]:
        parts.append("math signals (" + ", ".join(features["math_hits"][:4]) + ")")
    if features["factual_risk"]:
        parts.append("factual-risk signals (" + ", ".join(features["factual_hits"][:4]) + ")")
    if features["reasoning"]:
        parts.append("reasoning words (" + ", ".join(features["reasoning_hits"][:4]) + ")")
    short = "short prompt" if features["char_len"] <= 60 else f"{features['char_len']} chars"
    if features["multi_part"]:
        short += ", multi-part"
    signal_text = "; ".join(parts) if parts else "no code/math/factual/reasoning signals"
    return (
        f"{tier}: score {score:.2f} from {short} and {signal_text} "
        f"(nano < {NANO_THRESHOLD:.1f} <= super < {ULTRA_THRESHOLD:.1f} <= ultra)"
    )


def route_prompt(prompt: str) -> dict[str, Any]:
    """Route a prompt to a Nemotron tier, escalating if confidence < 0.70."""
    started = time.perf_counter()
    text = prompt or ""
    char_len = len(text)
    word_count = len(text.split())

    code_hits = _hits(text, _COMPILED_CODE)
    math_hits = _hits(text, _COMPILED_MATH)
    factual_hits = _hits(text, _COMPILED_FACTUAL)
    reasoning_hits = _hits(text, _COMPILED_REASONING)
    multi_part = bool(_NUMBERED_LIST.search(text)) or text.count("?") >= 2

    # Ignore arithmetic-only prompts such as "2 + 2"; they are not math workloads.
    if math_hits and all(hit.startswith("formula:") for hit in math_hits) and re.fullmatch(
        r"[\s\d+\-*/^().=]+", text
    ):
        math_hits = []

    signal_count = len(code_hits) + len(math_hits) + len(factual_hits) + len(reasoning_hits)
    score = _length_score(char_len, has_signals=signal_count > 0)
    score += min(2.3, 0.8 * len(code_hits))
    score += min(2.3, 0.8 * len(math_hits))
    score += min(3.0, 1.25 * len(factual_hits))
    score += min(2.7, 0.9 * len(reasoning_hits))
    if multi_part:
        score += 0.4
    score = round(score, 2)

    base_tier = _tier_for_score(score)
    confidence = _confidence(
        score,
        signal_count,
        strong_signal=bool(code_hits or math_hits or factual_hits),
    )
    tier = base_tier
    escalated = False
    escalation_note = ""
    if confidence < CONFIDENCE_FLOOR and tier != "ultra":
        tier = config.TIERS[config.TIER_RANK[tier] + 1]
        escalated = True
        escalation_note = (
            f"; confidence {confidence:.2f} < {CONFIDENCE_FLOOR:.2f} -> escalated {base_tier}->{tier}"
        )

    try:
        model = config.model_for_tier(tier)
    except config.ConfigError:
        model = tier

    features: dict[str, Any] = {
        "char_len": char_len,
        "word_count": word_count,
        "estimated_tokens": max(1, char_len // 4),
        "code": bool(code_hits),
        "math": bool(math_hits),
        "factual_risk": bool(factual_hits),
        "reasoning": bool(reasoning_hits),
        "multi_part": multi_part,
        "code_hits": code_hits,
        "math_hits": math_hits,
        "factual_hits": factual_hits,
        "reasoning_hits": reasoning_hits,
        "signal_count": len(code_hits) + len(math_hits) + len(factual_hits) + len(reasoning_hits),
        "score": score,
        "boundary_margin": round(_boundary_margin(score), 3),
        "base_tier": base_tier,
        "escalated": escalated,
    }

    routing_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "model": model,
        "tier": tier,
        "confidence": confidence,
        "reason": _describe(base_tier, features, score) + escalation_note,
        "features": features,
        "routing_ms": routing_ms,
    }
