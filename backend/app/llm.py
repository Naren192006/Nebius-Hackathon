"""Single entry point for talking to Nemotron on Nebius Token Factory."""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from openai import APIConnectionError, APITimeoutError, OpenAI

from . import config

logger = logging.getLogger("trustrouter.llm")

# One attempt plus at most two retries; never retry a client error such as 400
# or 401 (a bad request or a bad key will fail the same way every time).
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5
RETRY_MAX_DELAY = 8.0
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_CONCURRENT_CALLS = 4

# The OpenAI client is synchronous, so the concurrency cap is a thread semaphore.
_call_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)
_sleep = time.sleep

# Reasoning models sometimes emit <think>...</think> (or <thinking>). Strip whole
# blocks, then any unclosed trailing block, then stray tags.
_THINK_BLOCK = re.compile(r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?\s*>", re.IGNORECASE | re.DOTALL)
_THINK_UNCLOSED = re.compile(r"<think(?:ing)?\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)
_THINK_TAG = re.compile(r"</?think(?:ing)?\b[^>]*>", re.IGNORECASE)

_client: OpenAI | None = None


@dataclass(frozen=True)
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cost_usd: float
    model: str
    tier: str
    finish_reason: str | None = None
    mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_think_blocks(text: str) -> str:
    """Remove <think>/<thinking> reasoning blocks from model output."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub("", text)
    cleaned = _THINK_UNCLOSED.sub("", cleaned)
    cleaned = _THINK_TAG.sub("", cleaned)
    return cleaned.strip()


def calculate_cost(tier: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = config.price_for_tier(tier)
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


def _get_client() -> OpenAI:
    """Lazily construct the shared client so import never needs a key."""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.api_key(),
            base_url=config.base_url(),
            timeout=config.request_timeout(),
        )
    return _client


def _reset_client() -> None:
    """Test hook: drop the cached client after env changes."""
    global _client
    _client = None


def _is_retryable(exc: Exception) -> bool:
    """Rate limits, server errors, and timeouts are transient; 4xx is not."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_STATUS or status >= 500
    return isinstance(exc, (APITimeoutError, APIConnectionError))


def _retry_delay(attempt: int) -> float:
    """Exponential backoff (0.5s, 1s, ...) with a little jitter."""
    base = min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * (2**attempt))
    return base + random.uniform(0, 0.1)


def _create_completion(
    *,
    tier: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
) -> tuple[Any, int]:
    """Call once with retries; returns (completion, attempts_used)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with _call_slots:
                completion = _get_client().chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=config.request_timeout(),
                )
            return completion, attempt
        except Exception as exc:  # noqa: BLE001 - classified below
            retryable = _is_retryable(exc)
            if not retryable or attempt == MAX_ATTEMPTS:
                logger.warning(
                    "llm call failed tier=%s model=%s attempt=%d retryable=%s error=%s status=%s",
                    tier,
                    model,
                    attempt,
                    retryable,
                    type(exc).__name__,
                    getattr(exc, "status_code", None),
                )
                raise
            delay = _retry_delay(attempt - 1)
            logger.warning(
                "llm retry tier=%s model=%s attempt=%d error=%s status=%s delay_s=%.2f",
                tier,
                model,
                attempt,
                type(exc).__name__,
                getattr(exc, "status_code", None),
                delay,
            )
            _sleep(delay)
    raise RuntimeError("unreachable: retry loop exhausted")


def _as_openai_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        normalized.append({"role": role, "content": content})
    if not normalized:
        raise ValueError("messages must contain at least one message")
    return normalized


def _mock_result(tier: str, messages: list[dict[str, str]], started: float) -> LLMResult:
    """Deterministic offline stand-in. Used only when TRUSTROUTER_MOCK_LLM=1."""
    content = messages[-1]["content"]
    if "Draft answer:" in content:
        # Repair pass: return something clean instead of echoing the whole prompt.
        question = content.split("Draft answer:")[0].removeprefix("Question:").strip()
        text = (
            f"[mock:{tier}] Repaired answer for: {question[:120]}\n"
            "Any statement this gateway cannot support is now left out or hedged."
        )
    else:
        text = (
            f"[mock:{tier}] Answer for: {content[:120]}\n"
            "This response came from the local mock, not Nebius, and costs nothing."
        )
    tokens_in = max(1, len(content) // 4)
    tokens_out = max(1, len(text) // 4)
    return LLMResult(
        text=text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        cost_usd=0.0,
        model=f"mock/{tier}",
        tier=tier,
        finish_reason="stop",
        mock=True,
    )


def call_llm(
    tier: str,
    messages: Iterable[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> LLMResult:
    """Call one Nemotron tier and return text plus usage, latency, and cost."""
    if tier not in config.TIER_RANK:
        raise ValueError(f"unknown tier {tier!r}; expected one of {config.TIERS}")
    normalized = _as_openai_messages(messages)
    limit = max_tokens or config.default_max_tokens()
    started = time.perf_counter()

    if config.mock_llm():
        return _mock_result(tier, normalized, started)

    model = config.model_for_tier(tier)
    completion, attempts = _create_completion(
        tier=tier,
        model=model,
        messages=normalized,
        max_tokens=limit,
        temperature=temperature,
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    choice = completion.choices[0]
    text = strip_think_blocks(choice.message.content or "")
    usage = completion.usage
    tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
    cost_usd = calculate_cost(tier, tokens_in, tokens_out)

    logger.info(
        "llm ok tier=%s model=%s tokens_in=%d tokens_out=%d latency_ms=%.2f cost_usd=%.8f attempts=%d",
        tier,
        model,
        tokens_in,
        tokens_out,
        latency_ms,
        cost_usd,
        attempts,
    )

    return LLMResult(
        text=text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        model=model,
        tier=tier,
        finish_reason=getattr(choice, "finish_reason", None),
    )
