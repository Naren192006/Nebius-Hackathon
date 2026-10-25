"""Rule-based router. No LLM call, so routing overhead is ~1 ms.

Known limit: rules cannot see false premises (e.g. "Why did Newton win a Nobel?").
That is exactly what claim verification is for.
"""
import os
import re
import time

TIERS = ("nano", "super", "ultra")

FACTUAL = [
    r"\b(who|when|where|which)\b.{0,60}\b(wrote|invented|founded|won|born|died|released|discovered|published)\b",
    r"\b(1[5-9]|20)\d{2}\b",
    r"\b(cite|citation|paper|study|statistic|according to|source|journal)\b",
    r"\b(latest|current|currently|today|recent|news|price of)\b",
    r"\b(what year|which year|how many|how much|how old)\b",
]
REASONING = [r"\b(prove|derive|step[- ]by[- ]step|analy[sz]e|compare|trade-?offs?|design|optimi[sz]e|plan)\b"]
CODE = [r"```", r"\b(def|class|function|import|select|traceback|exception)\b"]
MATH = [r"\d\s*[-+*/^=]\s*\d", r"\b(integral|derivative|equation|probability|solve)\b"]


def _hits(patterns, text):
    return sum(bool(re.search(p, text)) for p in patterns)


def _distinct_words(patterns, text):
    """Number of distinct matching words (so 'compare ... design' counts as 2)."""
    found = set()
    for p in patterns:
        for m in re.finditer(p, text):
            found.add(m.group(0))
    return len(found)


def route_prompt(prompt: str, min_conf: float | None = None) -> dict:
    min_conf = float(os.getenv("ROUTER_MIN_CONF", "0.70")) if min_conf is None else min_conf
    t0 = time.perf_counter()
    low = prompt.lower()
    words = len(prompt.split())
    features = {
        "length_words": words,
        "has_code": _hits(CODE, low) > 0,
        "has_math": _hits(MATH, low) > 0,
        "factual_hits": _hits(FACTUAL, low),
        "reasoning_hits": _distinct_words(REASONING, low),
    }

    if words > 400 or features["reasoning_hits"] >= 2:
        tier, conf, reason = "ultra", 0.80, "long or multi-step reasoning prompt"
    elif features["factual_hits"] >= 2:
        tier, conf, reason = "ultra", 0.65, "several factual-risk signals"
    elif (features["factual_hits"] == 1 or features["has_code"] or features["has_math"]
          or features["reasoning_hits"] == 1):
        tier, conf, reason = "super", 0.70, "moderate risk signal"
    else:
        tier = "nano"
        conf = 0.85 if words <= 60 else 0.60
        reason = "short, low-risk prompt" if conf >= 0.85 else "no risk signal but long prompt"

    initial, upgraded = tier, False
    if conf < min_conf and tier != "ultra":
        tier = TIERS[TIERS.index(tier) + 1]
        upgraded = True
        reason += f"; confidence {conf:.2f} below {min_conf:.2f}, moved up from {initial}"

    return {
        "model": tier,
        "initial_model": initial,
        "confidence": conf,
        "reason": reason,
        "upgraded_for_confidence": upgraded,
        "features": features,
        "routing_ms": round((time.perf_counter() - t0) * 1000, 3),
    }
