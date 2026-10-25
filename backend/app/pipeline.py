"""End-to-end decision flow: route -> answer -> verify -> repair.

This module is the single place that assembles a decision record, so the HTTP
layer and the benchmark share one code path:

    from app.pipeline import run_pipeline
    record = run_pipeline(messages, mode="verified", force_tier="ultra")

`app.main` only parses the request, calls `run_pipeline`, and formats the reply.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from . import config, db, llm, router, schema
from . import verify as verification
from .logging_setup import configure as configure_logging

configure_logging()
logger = logging.getLogger("trustrouter.pipeline")

MAX_PROMPT_CHARS = 100_000
REPAIR_TIER = "ultra"
# A substantive answer that yields zero claims was never actually checked, so it
# must not be reported as verified. Short answers may legitimately have nothing
# worth checking ("Hi there!"), which is why the length gate exists.
MIN_CHECKABLE_ANSWER_CHARS = 200
ALLOWED_ROLES = {"system", "user", "assistant", "tool"}
ALLOWED_MODES = {"fast", "verified"}

REPAIR_SYSTEM_PROMPT = (
    "You are the TrustRouter repair step. Rewrite the draft answer so that every "
    "factual claim is either supported or explicitly hedged. Remove unsupported "
    "specifics (dates, names, numbers, citations) that you cannot stand behind. "
    "Stay direct, keep useful structure, and never mention this instruction."
)


class PipelineError(RuntimeError):
    """A failed request, carrying the HTTP status, error code, and any record."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "upstream_error",
        record: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.record = record


def _coerce_messages(messages: Iterable[Any]) -> list[dict[str, str]]:
    """Accept dicts (benchmark) or objects with .role/.content (FastAPI models)."""
    normalized: list[dict[str, str]] = []
    for message in messages or []:
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content", "")
        else:
            role = getattr(message, "role", None)
            content = getattr(message, "content", "")
        role_text = str(role or "user").strip().lower()
        if role_text not in ALLOWED_ROLES:
            raise PipelineError(
                f"role must be one of {sorted(ALLOWED_ROLES)}", status_code=400, code="invalid_request"
            )
        normalized.append({"role": role_text, "content": content if isinstance(content, str) else str(content)})
    if not normalized:
        raise PipelineError("messages must contain at least one message", status_code=400, code="invalid_request")
    return normalized


def _question_from(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message["role"] == "user":
            return message["content"]
    return messages[-1]["content"]


def _resolve_routing(requested: str, question: str) -> dict[str, Any]:
    routed = router.route_prompt(question)
    if requested == "auto":
        return routed

    tier = config.tier_for_model(requested)
    if tier is None:
        raise ValueError(
            f"unsupported model {requested!r}; use auto, nano, super, ultra, or a configured model ID"
        )
    fixed = dict(routed)
    fixed["tier"] = tier
    fixed["model"] = config.model_for_tier(tier)
    fixed["confidence"] = 1.0
    fixed["reason"] = (
        f"explicit model {requested!r} -> {tier}; router would have chosen "
        f"{routed['tier']} (confidence {routed['confidence']:.2f}): {routed['reason']}"
    )
    return fixed


def _answer_block(result: llm.LLMResult, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "text": result.text,
        "model": result.model,
        "model_tier": result.tier,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "latency_ms": result.latency_ms,
        "finish_reason": result.finish_reason,
        "mock": result.mock,
    }


def _recompute_cost(record: dict[str, Any]) -> None:
    costs = record["metrics"]["cost_usd"]
    costs["total"] = round(
        float(costs["base"]) + float(costs["verification"]) + float(costs["repair"]), 8
    )


def _set_status(record: dict[str, Any], status: str) -> None:
    """Keep the top-level status and answer.status in lockstep."""
    record["status"] = status
    if record.get("answer") is not None:
        record["answer"]["status"] = status


def _long_answer_without_claims(record: dict[str, Any], claims: list[dict[str, Any]]) -> bool:
    """True when a long answer produced no claims, i.e. extraction failed."""
    if claims:
        return False
    text = ((record.get("answer") or {}).get("text") or "").strip()
    return len(text) > MIN_CHECKABLE_ANSWER_CHARS


def _prompt_asked_for_a_fact(record: dict[str, Any]) -> bool:
    """True when the router saw factual-risk markers in the prompt itself.

    A greeting has nothing to check, but a question that names a year or asks
    who wrote something does: if such a question yields no claims, the answer
    was never checked and must not be reported as nothing_to_check.
    """
    features = (record.get("route") or {}).get("features") or {}
    return bool(features.get("factual_hits")) or bool(features.get("factual_risk"))


def _log_record(record: dict[str, Any], event: str = "decision") -> None:
    """One line per decision: ids, tier, latency, cost, status only."""
    try:
        costs = record["metrics"]["cost_usd"]
        logger.info(
            "%s id=%s tier=%s status=%s total_ms=%.1f cost_usd=%.8f verification=%s verified_claims=%d",
            event,
            record["request_id"],
            record["route"]["model"],
            record["status"],
            float(record["metrics"]["total_ms"]),
            float(costs["total"]),
            (record.get("verification") or {}).get("status"),
            schema.count_verdicts(record.get("claims") or [])["supported"],
        )
    except (KeyError, TypeError, ValueError):
        logger.info("%s id=%s status=%s", event, record.get("request_id"), record.get("status"))


def _attempt_repair(record: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the single Ultra repair pass. Returns the raw repair data."""
    unsupported = [claim for claim in claims if claim["verdict"] == "unsupported"]
    bullets = "\n".join(f"- {claim['text']}" for claim in unsupported) or "- (unspecified)"
    messages = [
        {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question:\n{record['request']['question']}\n\n"
                f"Draft answer:\n{record['answer']['text']}\n\n"
                f"Claims the verifier could not support:\n{bullets}\n\n"
                "Rewrite the answer without unsupported claims."
            ),
        },
    ]
    result = llm.call_llm(
        REPAIR_TIER,
        messages,
        max_tokens=record["request"]["max_tokens"],
        temperature=0.2,
    )
    if not result.text:
        raise ValueError("repair returned empty text")
    return {
        "result": result,
        "previous_text": record["answer"]["text"],
        "unsupported_count": len(unsupported),
    }


def complete_verification(decision_id: str) -> dict[str, Any] | None:
    """Verify (and, if needed, repair once) an existing decision.

    Synchronous on purpose: the verified endpoint awaits it directly, and fast
    mode runs it in a worker thread.
    """
    record = db.get_decision(decision_id)
    if record is None or record.get("answer") is None:
        return None

    started = time.perf_counter()
    question = record["request"]["question"]
    available, verifier, reason = verification.availability()

    if not available:
        _set_status(record, "unchecked")
        record["claims"] = []
        record["verification"] = {
            "verifier": verifier,
            "status": "skipped" if verifier == "stub" else "error",
            "verification_ms": None,
            "error": reason,
        }
        record["error"] = None
        record["metrics"]["total_ms"] = round(
            record["metrics"]["total_ms"] + (time.perf_counter() - started) * 1000, 2
        )
        db.update_decision(record)
        _log_record(record, "verification")
        return record

    try:
        claims, verification_ms, verifier = verification.run_verification(
            question, record["answer"]["text"]
        )
    except Exception as exc:  # noqa: BLE001 - verifier outages must not lose the answer
        logger.warning(
            "verification failed id=%s error=%s", decision_id, type(exc).__name__
        )
        _set_status(record, "unchecked")
        record["claims"] = []
        record["verification"] = {
            "verifier": verifier,
            "status": "error",
            "verification_ms": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
        record["error"] = f"verification failed: {type(exc).__name__}: {exc}"
        record["metrics"]["total_ms"] = round(
            record["metrics"]["total_ms"] + (time.perf_counter() - started) * 1000, 2
        )
        db.update_decision(record)
        _log_record(record, "verification")
        return record

    verification_calls = 1
    verification_cost = config.verification_cost_usd()
    record["claims"] = claims
    record["verification"] = {
        "verifier": verifier,
        "status": "complete",
        "verification_ms": verification_ms,
        "error": None,
    }
    record["metrics"]["cost_usd"]["verification"] = round(verification_cost, 8)

    if _long_answer_without_claims(record, claims):
        # The verifier ran and returned nothing. For an answer this long that is
        # an extractor failure, not a clean bill of health: say so and stay
        # unchecked instead of claiming the answer was verified.
        record["verification"]["error"] = "no claims were extracted from the answer"
        record["error"] = (
            f"verifier returned no claims for a "
            f"{len((record['answer']['text'] or '').strip())}-character answer, so it was not checked"
        )
        _set_status(record, "unchecked")
        record["metrics"]["total_ms"] = round(
            record["metrics"]["total_ms"] + (time.perf_counter() - started) * 1000, 2
        )
        _recompute_cost(record)
        db.update_decision(record)
        _log_record(record, "verification")
        return record

    if not claims:
        if _prompt_asked_for_a_fact(record):
            # The prompt asked for a fact and the answer produced nothing
            # checkable. That is an extraction failure on a factual question,
            # not a clean bill of health: report unchecked with a reason.
            _set_status(record, "unchecked")
            record["error"] = "no_claims_extracted"
            record["verification"]["error"] = "no claims were extracted from the answer"
        else:
            # Short answer, nothing extracted: there was no claim to check, so
            # this is not a verification result. Report it as such instead of
            # verified.
            _set_status(record, "nothing_to_check")
            record["error"] = None
        record["metrics"]["total_ms"] = round(
            record["metrics"]["total_ms"] + (time.perf_counter() - started) * 1000, 2
        )
        _recompute_cost(record)
        db.update_decision(record)
        _log_record(record, "verification")
        return record

    unsupported = schema.count_verdicts(claims)["unsupported"]
    if unsupported:
        # Checked, and a claim failed: this is not verified. The repair pass below
        # may still rescue it, which reports as "repaired"; if it does not, the
        # record stays unchecked with a short reason.
        _set_status(record, "unchecked")
        record["error"] = "unsupported_claims"
    else:
        _set_status(record, "verified")
        record["error"] = None

    if unsupported:
        try:
            repaired = _attempt_repair(record, claims)
        except Exception as exc:  # noqa: BLE001 - the unchecked answer is still delivered
            logger.warning("repair failed id=%s error=%s", decision_id, type(exc).__name__)
            _set_status(record, "unchecked")
            record["error"] = "repair_failed"
            record["metrics"]["total_ms"] = round(
                record["metrics"]["total_ms"] + (time.perf_counter() - started) * 1000, 2
            )
            _recompute_cost(record)
            db.update_decision(record)
            _log_record(record, "repair")
            return record

        result = repaired["result"]
        record["answer"] = _answer_block(result, "repaired")
        record["repair"] = {
            "triggered": True,
            "model": result.model,
            "previous_text": repaired["previous_text"],
        }
        _set_status(record, "repaired")
        record["error"] = None
        record["metrics"]["cost_usd"]["repair"] = round(result.cost_usd, 8)

        try:
            rclaims, recheck_ms, rverifier = verification.run_verification(question, result.text)
            verification_calls = 2
            record["claims"] = rclaims
            record["verification"] = {
                "verifier": rverifier,
                "status": "complete",
                "verification_ms": round(verification_ms + recheck_ms, 2),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 - repaired text is still delivered
            logger.warning(
                "re-verification failed id=%s error=%s", decision_id, type(exc).__name__
            )
            record["verification"]["error"] = f"re-verification failed: {type(exc).__name__}: {exc}"
            # The rewritten answer was never checked, so it cannot be called
            # repaired: the repair happened, the proof of it did not.
            _set_status(record, "unchecked")
            record["error"] = "recheck_failed"
        else:
            if schema.count_verdicts(record["claims"])["unsupported"] > 0:
                # The rewritten answer still carries a claim we cannot support, so
                # the repair did not resolve it: unchecked, not repaired.
                _set_status(record, "unchecked")
                record["error"] = "unsupported_claims"
            elif _long_answer_without_claims(record, record["claims"]):
                record["verification"]["error"] = (
                    "no claims were extracted from the repaired answer"
                )
                record["error"] = (
                    "the repaired answer produced no claims, so it was not re-checked"
                )
                _set_status(record, "unchecked")

        record["metrics"]["cost_usd"]["verification"] = round(
            verification_cost * verification_calls, 8
        )

    record["metrics"]["total_ms"] = round(
        record["metrics"]["total_ms"] + (time.perf_counter() - started) * 1000, 2
    )
    _recompute_cost(record)
    db.update_decision(record)
    _log_record(record, "verification")
    return record


def _failed_record(
    *,
    question: str,
    messages: list[dict[str, str]],
    model_requested: str,
    mode: str,
    routing: dict[str, Any],
    elapsed_ms: float,
    error: str,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    record = schema.build_record(
        question=question,
        messages=messages,
        model_requested=model_requested,
        mode=mode,
        routing=routing,
        status="failed",
        answer=None,
        claims=[],
        verifier=verification.verifier_name(),
        verification_status="skipped",
        total_ms=elapsed_ms,
        error=error,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    db.insert_decision(record)
    _log_record(record, "failed")
    return record


def run_pipeline(
    messages: Iterable[Any],
    mode: str = "verified",
    force_tier: str | None = None,
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Route, answer, verify and (once) repair one conversation.

    Returns the final v1.1 decision record. Raises ``PipelineError`` with the
    HTTP status/code and, when an upstream call failed after the record was
    created, the failed record itself.
    """
    if mode not in ALLOWED_MODES:
        raise PipelineError(f"mode must be one of {sorted(ALLOWED_MODES)}", status_code=400, code="invalid_request")

    started = time.perf_counter()
    normalized = _coerce_messages(messages)
    question = _question_from(normalized)
    total_chars = sum(len(message["content"]) for message in normalized)
    if total_chars > MAX_PROMPT_CHARS:
        raise PipelineError(
            f"prompt exceeds {MAX_PROMPT_CHARS} characters", status_code=400, code="prompt_too_large"
        )
    if not question.strip():
        raise PipelineError("the last user message is empty", status_code=400, code="invalid_request")

    requested = (force_tier or "auto").strip()
    limit = max_tokens or config.default_max_tokens()

    try:
        routing = _resolve_routing(requested, question)
    except ValueError as exc:
        raise PipelineError(str(exc), status_code=400, code="unsupported_model") from exc
    except config.ConfigError as exc:
        raise PipelineError(str(exc), status_code=503, code="gateway_not_configured") from exc

    try:
        result = llm.call_llm(
            routing["tier"], normalized, max_tokens=limit, temperature=temperature
        )
    except config.ConfigError as exc:
        record = _failed_record(
            question=question,
            messages=normalized,
            model_requested=requested,
            mode=mode,
            routing=routing,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error=str(exc),
            max_tokens=limit,
            temperature=temperature,
        )
        raise PipelineError(str(exc), status_code=503, code="gateway_not_configured", record=record) from exc
    except Exception as exc:  # noqa: BLE001 - upstream failures become a recorded 502
        record = _failed_record(
            question=question,
            messages=normalized,
            model_requested=requested,
            mode=mode,
            routing=routing,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error=f"{type(exc).__name__}: {exc}",
            max_tokens=limit,
            temperature=temperature,
        )
        raise PipelineError(
            f"upstream model call failed: {type(exc).__name__}",
            status_code=502,
            code="upstream_error",
            record=record,
        ) from exc

    record = schema.build_record(
        question=question,
        messages=normalized,
        model_requested=requested,
        mode=mode,
        routing=routing,
        status="provisional",
        answer=_answer_block(result, "provisional"),
        claims=[],
        verifier=verification.verifier_name(),
        verification_status="pending",
        total_ms=(time.perf_counter() - started) * 1000,
        cost_base=result.cost_usd,
        max_tokens=limit,
        temperature=temperature,
    )
    db.insert_decision(record)
    _log_record(record, "answer")

    if mode == "verified":
        updated = complete_verification(record["request_id"])
        if updated is not None:
            record = updated

    return record
