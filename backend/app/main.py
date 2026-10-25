"""TrustRouter gateway API (decision schema v1.1).

POST /v1/chat/completions  OpenAI-compatible chat with `mode` and `model` extras.
GET  /decisions/{id}       Full decision record.
GET  /decisions            Recent decision summaries.
GET  /healthz              Config/health probe.
GET  /health               Liveness probe ({"ok": true}).

The decision flow itself lives in `app.pipeline`; this module only parses the
request, calls it, and formats the reply.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config, db, pipeline, schema
from . import verify as verification
from .logging_setup import configure as configure_logging

logger = logging.getLogger("trustrouter.api")

ALLOWED_ROLES = {"system", "user", "assistant", "tool"}


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str
    name: str | None = None

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: str) -> str:
        role = value.strip().lower()
        if role not in ALLOWED_ROLES:
            raise ValueError(f"role must be one of {sorted(ALLOWED_ROLES)}")
        return role


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "auto"
    # Empty is validated in the handler so the gateway can answer 400 (not 422).
    messages: list[ChatMessage] = Field(default_factory=list)
    mode: Literal["fast", "verified"] = "fast"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    stream: bool = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_file = configure_logging()
    db.init_db()
    logger.info("database ready at %s (log: %s)", config.db_path(), log_file)
    if config.mock_llm():
        logger.warning("TRUSTROUTER_MOCK_LLM=1: answers come from the local mock")
    available, name, reason = verification.availability()
    if not available:
        logger.warning("verifier unavailable (%s): %s", name, reason)
    yield


app = FastAPI(
    title="TrustRouter Gateway",
    version="0.3.0",
    description="Routes prompts across Nemotron Nano/Super/Ultra and verifies answers.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_background: set[asyncio.Task[Any]] = set()


def _error(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "invalid_request_error" if status_code < 500 else "api_error",
                "param": None,
                "code": code,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
    return _error(400, f"{location}: {first.get('msg', 'invalid request')}", "invalid_request")


def _gateway_block(record: dict[str, Any]) -> dict[str, Any]:
    counts = schema.count_verdicts(record.get("claims") or [])
    return {
        "status": record["status"],
        "model_tier": record["route"]["model"],
        "decision_id": record["request_id"],
        "supported_claims": counts["supported"],
        "unsupported_claims": counts["unsupported"],
        "cost_usd": record["metrics"]["cost_usd"]["total"],
    }


async def _finish_in_background(decision_id: str) -> None:
    try:
        await asyncio.to_thread(pipeline.complete_verification, decision_id)
    except Exception:  # noqa: BLE001 - background work must never crash the loop
        logger.exception("background verification crashed for %s", decision_id)


def _spawn_background(decision_id: str) -> None:
    task = asyncio.create_task(_finish_in_background(decision_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _created_epoch(record: dict[str, Any]) -> int:
    try:
        parsed = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except ValueError:
        return int(datetime.now(timezone.utc).timestamp())


def _chat_response(record: dict[str, Any]) -> dict[str, Any]:
    answer = record["answer"] or {}
    tokens_in = int(answer.get("tokens_in", 0))
    tokens_out = int(answer.get("tokens_out", 0))
    return {
        "id": f"chatcmpl-{record['request_id'][4:]}",
        "object": "chat.completion",
        "created": _created_epoch(record),
        "model": record["route"]["model_id"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer.get("text", "")},
                "finish_reason": answer.get("finish_reason") or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
        "gateway": _gateway_block(record),
    }


def _failure_response(
    status_code: int,
    message: str,
    code: str,
    record: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {"message": message, "type": "api_error", "param": None, "code": code}
    }
    if record is not None:
        body["gateway"] = _gateway_block(record)
    return JSONResponse(status_code=status_code, content=body)


@app.post("/v1/chat/completions")
async def chat_completions(payload: ChatCompletionRequest) -> Any:
    if not payload.messages:
        return _error(
            400,
            "messages must contain at least one message",
            "empty_messages",
        )

    if payload.stream:
        return _error(
            400,
            "stream=true is not supported yet; the gateway returns complete responses only.",
            "streaming_not_supported",
        )

    messages = [{"role": message.role, "content": message.content} for message in payload.messages]
    try:
        record = await asyncio.to_thread(
            pipeline.run_pipeline,
            messages,
            payload.mode,
            payload.model,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
        )
    except pipeline.PipelineError as exc:
        if exc.record is None:
            return _error(exc.status_code, exc.message, exc.code)
        return _failure_response(exc.status_code, exc.message, exc.code, exc.record)

    if payload.mode == "fast" and record["status"] == "provisional":
        _spawn_background(record["request_id"])

    return _chat_response(record)


@app.get("/decisions/{decision_id}")
async def get_decision(decision_id: str) -> Any:
    record = await asyncio.to_thread(db.get_decision, decision_id)
    if record is None:
        return _error(404, f"decision {decision_id!r} not found", "decision_not_found")
    return record


@app.get("/decisions")
async def list_decisions(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    rows = await asyncio.to_thread(db.list_decisions, limit)
    return {"object": "list", "count": len(rows), "data": rows}


@app.get("/health")
async def health() -> dict[str, bool]:
    """Minimal liveness probe; `/healthz` is the detailed one."""
    return {"ok": True}


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    models: dict[str, str | None] = {}
    for tier in config.TIERS:
        try:
            models[tier] = config.model_for_tier(tier)
        except config.ConfigError:
            models[tier] = None
    available, verifier, reason = verification.availability()
    return {
        "status": "ok",
        "schema_version": schema.SCHEMA_VERSION,
        "models": models,
        "mock_llm": config.mock_llm(),
        "verifier": verifier,
        "verifier_available": available,
        "verifier_reason": reason,
        "db": str(config.db_path()),
    }
