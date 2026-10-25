from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import db  # noqa: E402
from app import verify as verification  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTROUTER_DB", str(tmp_path / "decisions.db"))
    monkeypatch.setenv("TRUSTROUTER_MOCK_LLM", "1")
    monkeypatch.setenv("TRUSTROUTER_LOG_PATH", str(tmp_path / "trustrouter.log"))
    monkeypatch.delenv("TRUSTROUTER_VERIFIER", raising=False)
    verification.set_verifier(None)
    db.init_db()
    yield
    verification.set_verifier(None)
