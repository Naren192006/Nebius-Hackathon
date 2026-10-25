"""Environment-backed configuration.

All secrets and model IDs live in backend/.env (gitignored). Nothing here
hard-codes a key, and every read happens at call time so tests can override
environment variables without re-importing modules.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Load once on import; existing environment variables win (override=False).
load_dotenv(BACKEND_DIR / ".env", override=False)

TIERS: tuple[str, ...] = ("nano", "super", "ultra")
TIER_RANK: dict[str, int] = {tier: rank for rank, tier in enumerate(TIERS)}
DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

_TIER_ENV = {"nano": "MODEL_NANO", "super": "MODEL_SUPER", "ultra": "MODEL_ULTRA"}
_PRICE_ENV = {
    "nano": ("PRICE_NANO_IN", "PRICE_NANO_OUT"),
    "super": ("PRICE_SUPER_IN", "PRICE_SUPER_OUT"),
    "ultra": ("PRICE_ULTRA_IN", "PRICE_ULTRA_OUT"),
}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def _require_tier(tier: str) -> str:
    if tier not in TIER_RANK:
        raise ConfigError(f"unknown tier {tier!r}; expected one of {TIERS}")
    return tier


def api_key() -> str:
    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "NEBIUS_API_KEY is not set. Put it in backend/.env (never commit it)."
        )
    return key


def base_url() -> str:
    return os.environ.get("NEBIUS_BASE_URL", "").strip() or DEFAULT_BASE_URL


def model_for_tier(tier: str) -> str:
    _require_tier(tier)
    value = os.environ.get(_TIER_ENV[tier], "").strip()
    if not value:
        raise ConfigError(f"{_TIER_ENV[tier]} is not set; run list_models.py and fill .env")
    return value


def price_for_tier(tier: str) -> tuple[float, float]:
    """Return (input, output) USD per 1M tokens."""
    _require_tier(tier)
    in_var, out_var = _PRICE_ENV[tier]
    try:
        return float(os.environ[in_var]), float(os.environ[out_var])
    except KeyError as exc:
        raise ConfigError(f"{exc.args[0]} is not set in backend/.env") from exc
    except ValueError as exc:
        raise ConfigError(f"invalid price for {tier}: {exc}") from exc


def tier_for_model(model: str) -> str | None:
    """Map an API `model` value (alias or full ID) to a tier, if it is one."""
    if model in TIER_RANK:
        return model
    for tier in TIERS:
        try:
            if model_for_tier(tier) == model:
                return tier
        except ConfigError:
            continue
    return None


def mock_llm() -> bool:
    return os.environ.get("TRUSTROUTER_MOCK_LLM", "0").strip().lower() in {"1", "true", "yes"}


def db_path() -> Path:
    raw = os.environ.get("TRUSTROUTER_DB", "").strip() or "./data/trustrouter.db"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


def default_max_tokens() -> int:
    try:
        return max(1, int(os.environ.get("TRUSTROUTER_DEFAULT_MAX_TOKENS", "512")))
    except ValueError:
        return 512


def log_path() -> Path:
    """Operational log file; override with TRUSTROUTER_LOG_PATH."""
    raw = os.environ.get("TRUSTROUTER_LOG_PATH", "").strip() or "logs/trustrouter.log"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


def request_timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("TRUSTROUTER_REQUEST_TIMEOUT", "60")))
    except ValueError:
        return 60.0


def verification_cost_usd() -> float:
    """Estimated spend per verifier call, until B reports real Tavily cost."""
    try:
        return max(0.0, float(os.environ.get("TRUSTROUTER_VERIFY_COST_USD", "0")))
    except ValueError:
        return 0.0


def cors_origins() -> list[str]:
    raw = os.environ.get("TRUSTROUTER_CORS_ORIGINS", "http://localhost:5173")
    # "*" is never a valid origin here: it would expose every site to the gateway.
    return [
        origin.strip()
        for origin in raw.split(",")
        if origin.strip() and origin.strip() != "*"
    ]
