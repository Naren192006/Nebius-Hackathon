# TrustRouter gateway API (Backend A, schema v1.1)

Base URL locally: `http://127.0.0.1:8000`. Interactive docs: `/docs`.
Everything is relative to `backend/`; secrets live only in `backend/.env`.

## Chat completions

`POST /v1/chat/completions`

```json
{
  "model": "auto",
  "mode": "verified",
  "max_tokens": 300,
  "messages": [{ "role": "user", "content": "Who wrote the novel Beloved?" }]
}
```

| Field | Values | Meaning |
| --- | --- | --- |
| `model` | `auto` \| `nano` \| `super` \| `ultra` \| configured model ID | `auto` runs the router; anything else pins that tier |
| `mode` | `fast` \| `verified` | `fast` returns `provisional` and verifies in the background; `verified` waits |
| `stream` | `false` only | `true` returns HTTP 400 `streaming_not_supported` |
| `temperature`, `max_tokens` | OpenAI-compatible | passed through (`max_tokens` capped at 8192) |

Response (counts are integers, matching Backend C's contract):

```json
{
  "id": "chatcmpl-1cd93c5706664c97873f7bee",
  "object": "chat.completion",
  "created": 1792599011,
  "model": "nvidia/nemotron-3-super-120b-a12b",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "..." },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 8, "completion_tokens": 31, "total_tokens": 39 },
  "gateway": {
    "status": "verified",
    "model_tier": "super",
    "decision_id": "dec_1cd93c5706664c97873f7bee",
    "supported_claims": 2,
    "unsupported_claims": 0,
    "cost_usd": 0.001
  }
}
```

`gateway.status`:

| Status | Meaning |
| --- | --- |
| `verified` | Verification ran, at least one claim was checked, and none were unsupported. |
| `repaired` | An unsupported claim was rewritten and the rewrite passed re-checking. |
| `unchecked` | Verification could not confirm the answer (no verifier, verifier error, unsupported claims left, repair or re-check failed, or a long answer with no claims). |
| `nothing_to_check` | A short answer with no claims to verify. |
| `provisional` | Fast-mode answer, verification still running. |

A claim marked `unclear` neither triggers repair nor blocks `verified`, so an answer whose
claims are all `unclear` shows `verified` with zero supported claims. `unclear` claims do not
appear in the counts; fetch the record for all three verdicts.

Error path: when the model call itself fails there is no answer to verify, so the record
carries `status: failed` with `answer: null` and HTTP 502/503 returns a `gateway` block with
counts `0`.

## Decisions

`GET /decisions/{id}` returns the full v1.1 record (404 `decision_not_found` if unknown).

`GET /decisions?limit=50` returns newest-first summaries:

```json
{
  "object": "list",
  "count": 1,
  "data": [
    {
      "decision_id": "dec_1cd93c5706664c97873f7bee",
      "request_id": "dec_1cd93c5706664c97873f7bee",
      "created_at": "2026-10-21T16:10:12.103Z",
      "updated_at": "2026-10-21T16:10:12.152Z",
      "status": "verified",
      "mode": "verified",
      "question": "Who wrote the novel Beloved?",
      "model_tier": "super",
      "model_id": "nvidia/nemotron-3-super-120b-a12b",
      "confidence": 1.0,
      "upgraded_for_confidence": false,
      "routing_ms": 0.11,
      "total_ms": 12.4,
      "reason": "explicit model 'super' -> super; ...",
      "tokens_in": 8,
      "tokens_out": 31,
      "cost_usd": 0.001,
      "cost_breakdown": {
        "base": 0.0,
        "verification": 0.001,
        "repair": 0.0,
        "total": 0.001
      },
      "supported_claims": 2,
      "unsupported_claims": 0,
      "unclear_claims": 0,
      "verification_status": "complete",
      "error": null
    }
  ]
}
```

`GET /healthz` reports model IDs, mock flag, verifier availability, schema version, and DB path.

## Decision record v1.1

The source of truth is
[`schema/decision_record.schema.json`](../schema/decision_record.schema.json). Records are
validated in `app/schema.py` on every insert/update. A real full example is
[`samples/records/verified.json`](../samples/records/verified.json).

```text
schema_version, request_id, created_at, updated_at, mode, status,
request { question, model_requested, messages, max_tokens, temperature }
route   { model: nano|super|ultra, model_id, confidence, reason,
          upgraded_for_confidence, features }
answer  { status, text, model, model_tier, tokens_in, tokens_out,
          latency_ms, finish_reason, mock } | null
claims  [{ text, verdict: supported|unsupported|unclear,
           source_url|null, evidence_snippet|null }]
repair  { triggered, model, previous_text } | null
metrics { routing_ms, total_ms,
          cost_usd { base, verification, repair, total } }
verification { verifier, status, verification_ms, error }   # additive audit block
error   string|null
```

Semantics that matter to the UI:

- `request_id` and `gateway.decision_id` are the same value.
- `route.model` is the tier; `route.model_id` is the full Token Factory ID.
- `route.upgraded_for_confidence` is true when the router raised the tier because confidence
  was below `0.70`. `route.features` carries the full evidence for “Why this model?”.
- `answer.text` is the **delivered** answer. When `repair` exists, `repair.previous_text` is
  the first-pass text, so the diff is `previous_text` → `answer.text`.
- `answer.status` mirrors the top-level `status`.
- `metrics.cost_usd.total` is `base + verification + repair`; `gateway.cost_usd` uses it.
- `verification` is additive audit metadata; the public claim list is top-level `claims`.
- Model failure records have `status: failed`, `answer: null`, and an `error`.

## Routing rules

`app/router.py` scores lexical features and maps the score to a tier:

- length: `< 60` chars with no signals → `-0.5`; `>= 300` → `+0.75`; `>= 800` → `+1.5`; `>= 2000` → `+2.0`
- code signals (0.8 each, cap 2.3): fenced code, language names, `def`/`class`/`function`, stack traces
- math signals (0.8 each, cap 2.3): calculus/algebra/proof terms, formulas; bare arithmetic is ignored
- factual-risk signals (1.25 each, cap 3.0): years, “who wrote”, citations, “latest”, fact lookups
- reasoning signals (0.9 each, cap 2.7): explain/compare/analyze/design/recommend/trade-offs
- multi-part prompts: `+0.4`

`score < 1.0` → nano, `< 3.0` → super, otherwise ultra. If confidence is below `0.70`,
the router goes up exactly one tier (ultra stays ultra).

## Verification contract (Backend B)

The gateway loads `verify(question, answer)` from `TRUSTROUTER_VERIFIER=module:function`.
The preferred return type is a list of:

```json
{
  "text": "claim text",
  "verdict": "supported | unsupported | unclear",
  "source_url": "https://... or null",
  "evidence_snippet": "short quote or null"
}
```

Compatibility: boolean `supported`, `verdict` aliases (`refuted`, `verified`, `unverified`, …),
`source_url`/`url`/`source`, `evidence_snippet`/`snippet`/`quote`/`evidence`, and
`{"claims": [...]}` wrappers are accepted. Plain strings become `unclear`; at most 50 claims
are kept.

Availability is explicit:

- no `TRUSTROUTER_VERIFIER` (or the stub) → `unchecked` with no claims
- target fails to import → `unchecked` with `verification.error`
- verifier runs, claims present, none unsupported → `verified`
- verifier runs with an unsupported claim → one Ultra repair, then `repaired` only if the
  rewrite re-checks with no unsupported claim, otherwise `unchecked`
- verifier runs and returns zero claims → `nothing_to_check` for a short answer with no
  factual-risk signal, `unchecked` with `error: "no_claims_extracted"` when the prompt did
  carry one (a year, "who wrote", a citation), and `unchecked` for a long one

Failure handling: a verifier exception leaves the answer intact, records
`verification.status = "error"`, and sets the decision to `unchecked`. A repair failure or a
failed re-check also leaves the answer `unchecked` and records `error` as `repair_failed` or
`recheck_failed`.

Repair policy: one pass maximum, always on **Ultra**, only when at least one claim is
`unsupported`; the repaired text is verified once more. Verification spend is estimated with
`TRUSTROUTER_VERIFY_COST_USD` per call until B reports real Tavily cost.

## CORS and polling

`TRUSTROUTER_CORS_ORIGINS` defaults to exactly `http://localhost:5173`, so the Vite app can
call the gateway directly and no other origin can; `*` is dropped if it is ever set. Fast mode is poll-only: call `GET /decisions/{id}`
until `status` leaves `provisional` (bounded timeout in the UI). SSE is not implemented yet.

## Samples for Backend C

- [provisional](../samples/provisional.json) — fast mode response, `status: provisional`
- [verified](../samples/verified.json) — verified response with claims
- [repaired](../samples/repaired.json) — repaired response with counts
- [unchecked](../samples/unchecked.json) — stub verifier, no claims
- Full records: [verified](../samples/records/verified.json),
  [repaired](../samples/records/repaired.json),
  [unchecked](../samples/records/unchecked.json)

They are offline captures (`TRUSTROUTER_MOCK_LLM=1`); see
[samples/README.md](../samples/README.md) for what is real and what is mocked.

## Pricing

Configured in `.env`, USD per 1M tokens: nano `0.06/0.24`, super `0.30/0.90`,
ultra `1.00/3.00`. Cost is computed from API usage; mock answers have `base = 0`.

## Known limitations

- Live model IDs/prices are seeded from the Nebius cookbook; run `python list_models.py`
  with a key to confirm the authenticated catalog.
- Fast-mode verification is an in-process `asyncio` task; a restart can leave a record
  `provisional` with `verification.status = "pending"`.
- No auth or rate limiting; this is a local dev gateway.
- Streaming is rejected by design.
- Old v1.0 records are not migrated; the dev SQLite file was reset when the schema moved to
  v1.1.
