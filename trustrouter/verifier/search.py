"""Tavily web search for a single claim.

Search is best-effort: any failure (missing key, network, bad response) returns []
and prints one short warning, so verification degrades to "unclear" instead of
crashing.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from tavily import AsyncTavilyClient

# Same .env as the gateway, so one file configures both.
_ENV = Path(__file__).resolve().parent.parent / "backend" / ".env"
if _ENV.exists():
    load_dotenv(_ENV)
else:
    load_dotenv()

MAX_RESULTS = 3
MAX_QUERY_CHARS = 380
MAX_SNIPPET_CHARS = 500
REQUEST_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 2  # one attempt plus a single retry
RETRY_DELAY_S = 0.5


def tavily_configured() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _redact(message: str, secret: str) -> str:
    return message.replace(secret, "[redacted]") if secret else message


async def search_evidence(claim: str) -> list[dict]:
    """Return up to 3 {"url", "snippet"} items for a claim. Never raises."""
    query = _clean(claim)[:MAX_QUERY_CHARS]
    if not query:
        return []

    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        print("[search] warning: TAVILY_API_KEY is not set, so no evidence was retrieved.")
        return []

    client = AsyncTavilyClient(api_key=api_key)
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.search(
                query=query, max_results=MAX_RESULTS, timeout=REQUEST_TIMEOUT_S
            )
            results = response.get("results") or []
            evidence = []
            for item in results[:MAX_RESULTS]:
                url = (item.get("url") or "").strip()
                if not url:
                    continue
                snippet = _clean(item.get("content") or item.get("raw_content") or "")
                evidence.append({"url": url, "snippet": snippet[:MAX_SNIPPET_CHARS]})
            return evidence
        except Exception as exc:  # noqa: BLE001 - search must never break verification
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY_S)

    detail = _redact(f"{type(last_error).__name__}: {last_error}", api_key)
    print(f"[search] warning: search failed for {query[:60]!r} - {detail[:240]}")
    return []
