"""File logging for the gateway: logs/trustrouter.log by default.

Only operational fields are written (request id, tier, latency, cost, status).
Callers must never pass prompts, message text, or credentials to the logger.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import config

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_configured: Path | None = None


def configure() -> Path:
    """Attach one file handler to the `trustrouter` logger; safe to call often."""
    global _configured
    path = config.log_path()
    if _configured == path:
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("trustrouter")
    root.setLevel(logging.INFO)

    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == path:
            _configured = path
            return path

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(handler)
    _configured = path
    return path


def log_path() -> Path:
    return config.log_path()
