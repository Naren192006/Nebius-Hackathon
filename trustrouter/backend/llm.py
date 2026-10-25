"""Thin wrapper around Token Factory (OpenAI-compatible) with cost + latency tracking."""
import os
import re
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

TIERS = ("nano", "super", "ultra")
_client = None


def _client_():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"),
            api_key=os.environ["NEBIUS_API_KEY"],
        )
    return _client


def model_id(tier: str) -> str:
    value = os.getenv(f"MODEL_{tier.upper()}")
    if not value:
        raise RuntimeError(f"MODEL_{tier.upper()} is empty. Run list_models.py and fill .env")
    return value


def price(tier: str) -> tuple[float, float]:
    """USD per 1M tokens (input, output)."""
    return (
        float(os.getenv(f"PRICE_{tier.upper()}_IN", "0")),
        float(os.getenv(f"PRICE_{tier.upper()}_OUT", "0")),
    )


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


@dataclass
class LLMResult:
    text: str
    tier: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cost_usd: float
    finish_reason: str | None = None


def cost_of(tier: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = price(tier)
    return (tokens_in * p_in + tokens_out * p_out) / 1_000_000


def call_llm(tier: str, messages: list[dict], temperature: float = 0.2,
             max_tokens: int = 300) -> LLMResult:
    """Call one tier. Keep max_tokens small while testing to protect credits."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    mid = model_id(tier)
    t0 = time.perf_counter()
    resp = _client_().chat.completions.create(
        model=mid, messages=messages, temperature=temperature, max_tokens=max_tokens
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    usage = resp.usage
    tin = getattr(usage, "prompt_tokens", 0) or 0
    tout = getattr(usage, "completion_tokens", 0) or 0
    text = strip_think(resp.choices[0].message.content or "")
    return LLMResult(text, tier, mid, tin, tout, round(latency_ms, 1), cost_of(tier, tin, tout),
                     getattr(resp.choices[0], "finish_reason", None))
