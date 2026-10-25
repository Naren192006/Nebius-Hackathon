# TrustRouter backend (Backend A)

Gateway that routes each prompt to NVIDIA Nemotron Nano, Super, or Ultra on Nebius Token
Factory, verifies the answer claim by claim, repairs with Ultra when needed, and adds a
`gateway` block to an OpenAI-compatible chat response. Python 3.11, FastAPI, SQLite,
`openai` client. Decision records follow the shared **v1.1** contract used by Backend C.

Everything lives in `backend/`. `NEBIUS_API_KEY` is read from `backend/.env`, which is
gitignored; no key or secret is stored anywhere else.

## Quickstart

```bash
cd backend
python -m venv .venv

# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env        # then paste NEBIUS_API_KEY into .env

# 1. Confirm the live model catalog and the three IDs in .env
python list_models.py

# 2. Free offline smoke test first, then the real one
python scripts/test_tiers.py --mock
python scripts/test_tiers.py

# 3. Route 10 prompts and persist each decision
python scripts/route_samples.py

# 4. Run the gateway
python -m uvicorn app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000/docs> or:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"auto","mode":"verified","max_tokens":100,
       "messages":[{"role":"user","content":"Who wrote the novel Beloved?"}]}'
```

`TRUSTROUTER_MOCK_LLM=1` swaps in a deterministic offline model (no network, no credits),
which is what the tests and sample captures use.

## What is here

```text
backend/
  app/
    config.py       env-backed models, prices, paths; no hard-coded secrets
    llm.py          call_llm(tier, messages) -> text, tokens, latency, cost; 60s timeout,
                    2 retries on 429/5xx/timeouts, 4 concurrent calls max
    router.py       route_prompt() -> model, confidence, reason, features, routing_ms
    schema.py       v1.1 record schema loader/validator + canonical build_record()
    db.py           decisions(id, created_at, status, record_json) + summaries
    verify.py       Backend B seam (module:function), three-state claims, availability
    pipeline.py     run_pipeline(messages, mode, force_tier) -> decision record (also
                    used by the benchmark); owns verify/repair and per-request logging
    logging_setup.py  logs/trustrouter.log: id, tier, latency, cost, status only
    main.py         FastAPI app: parses requests, calls run_pipeline, formats replies
  schema/decision_record.schema.json   shared record contract
  scripts/
    test_tiers.py       one tiny prompt per tier (max_tokens=100)
    route_samples.py    10 prompts -> routes -> saved decisions -> printed reasons
    make_samples.py     captures four responses + full records for Backend C
    demo_verifier.py    demo-only verifier used by make_samples (not Backend B)
  samples/              provisional, verified, repaired, unchecked + records/
  tests/                50 offline tests (mocked LLM, stub/demo verifiers)
  docs/api.md           API, v1.1 record, verifier contract, CORS, pricing, limits
```

## Configuration (`backend/.env`)

| Variable | Purpose |
| --- | --- |
| `NEBIUS_API_KEY` | Token Factory key. Required for live calls; empty by default |
| `NEBIUS_BASE_URL` | defaults to `https://api.tokenfactory.nebius.com/v1/` |
| `MODEL_NANO` / `MODEL_SUPER` / `MODEL_ULTRA` | exact catalog IDs |
| `PRICE_*_IN` / `PRICE_*_OUT` | USD per 1M tokens |
| `TRUSTROUTER_DB` | SQLite path, default `./data/trustrouter.db` |
| `TRUSTROUTER_DEFAULT_MAX_TOKENS` | default `512` |
| `TRUSTROUTER_REQUEST_TIMEOUT` | upstream timeout seconds, default `60` |
| `TRUSTROUTER_VERIFY_COST_USD` | estimated spend per verifier call, default `0` |
| `TRUSTROUTER_CORS_ORIGINS` | allowed browser origin(s), default `http://localhost:5173` only |
| `TRUSTROUTER_MOCK_LLM` | `1` = local mock, no credits |
| `TRUSTROUTER_LOG_PATH` | file log, default `./logs/trustrouter.log` (gitignored) |
| `TRUSTROUTER_VERIFIER` | `module:function` for Backend B's verifier |

Prices seeded from Nebius's public Nemotron page (Oct 2026): nano 0.06/0.24, super 0.30/0.90,
ultra 1.00/3.00 USD per 1M tokens. Model IDs come from the Nebius Token Factory cookbook
(`nvidia/nvidia-nemotron-3-nano-30b-a3b`, `nvidia/nemotron-3-super-120b-a12b`,
`nvidia/Nemotron-3-Ultra-550b-a55b`); `list_models.py` is the authority and prints which
`.env` values are missing from the live catalog.

## API summary

- `POST /v1/chat/completions` — OpenAI-compatible; adds `mode: fast|verified`, `model:
  auto|nano|super|ultra`; rejects `stream=true` with 400; response gains
  `gateway {status, model_tier, decision_id, supported_claims, unsupported_claims, cost_usd}`
  with **integer** claim counts.
- `GET /decisions/{id}` — full v1.1 record.
- `GET /decisions?limit=50` — newest-first summaries with cost breakdown and claim counts.
- `GET /healthz` — models, verifier availability, schema version, DB path.

Fast mode saves a `provisional` record and schedules verification in a background task;
verified mode waits. One repair pass on **Ultra** runs at most once, when a claim is
unsupported, and the rewritten answer is re-verified. With no verifier configured the result
is `unchecked`, never a false `verified`. See [docs/api.md](docs/api.md) for request/response
examples and the exact record shape.

Statuses:

| Status | Meaning |
| --- | --- |
| `verified` | Verification ran, at least one claim was checked, and none were unsupported. |
| `repaired` | An unsupported claim was rewritten and the rewrite passed re-checking. |
| `unchecked` | Verification could not confirm the answer (no verifier, verifier error, unsupported claims left, repair or re-check failed, or a long answer with no claims). |
| `nothing_to_check` | A short answer with no claims to verify. |
| `provisional` | Fast-mode answer, verification still running. |

A claim marked `unclear` neither triggers repair nor blocks `verified`, so an answer whose
claims are all `unclear` shows `verified` with zero supported claims.

## Delivered checks

| Check | Evidence |
| --- | --- |
| venv + runtime deps | `openai 3.36.0`, `fastapi 0.157.0`, `uvicorn 0.54.0`, `python-dotenv 1.2.4` |
| `call_llm` + `<think>` stripping | `scripts/test_tiers.py --mock`; `tests/test_llm.py` |
| router + escalation | `tests/test_router.py`; `route_samples.py` shows `0.65 < 0.70 -> nano->super` |
| 10 routed decisions saved | `python scripts/route_samples.py` → `routed=10 saved_records=10` |
| v1.1 record contract | `schema/decision_record.schema.json`, validated on every write |
| gateway counts + statuses | `tests/test_api.py` covers provisional, verified, repaired, unchecked, failed; `tests/test_core.py` covers nothing_to_check |
| repair on Ultra + re-verify | repaired test asserts `[mock:ultra]`, `previous_text`, and cost breakdown |
| decisions endpoints + CORS | `GET /decisions/{id}`, `GET /decisions`, preflight test |
| C sample payloads | four responses + three full records under `samples/` |
| reusable pipeline | `tests/test_core.py` calls `run_pipeline` directly with mocked LLM/verifier |
| retry + concurrency policy | `tests/test_core.py`: 429/5xx retried twice, 400/401 never, peak concurrency ≤ 4 |
| file logging | `logs/trustrouter.log` holds id, tier, latency, cost, status — no prompts or keys |

Tests: `python -m pytest -q` → **50 passed**.

Live call verified on 25 Oct 2026: all three tiers answer through Token Factory.
That smoke test was one single call per tier; no latency or cost figures beyond
those single calls were measured.

## Measured runs

Individual requests recorded against the running gateway. These are single calls, not a
benchmark, and none of them exercises the harder prompt categories.

- Single verified request, decision `dec_fb56320c0a2d4bd1832d9f74`: status `verified`,
  7 supported, 0 unsupported, `nano`. Single request, not a benchmark.
- Three requests (decisions `dec_5db279fee52048b0be2d70c6`,
  `dec_5419906a6cbf4b7aa0a5b2e1` (pre-fix, reads `nothing_to_check`),
  `dec_24d52de773c24d5ab18ed7b3`): recorded before the `no_claims_extracted` fix.

Verification cost is a fixed estimate of $0.03 per verified request
(`TRUSTROUTER_VERIFY_COST_USD`), not a per-call measurement.

The extractor can miss invented claims; an unverifiable factual answer is flagged
`unchecked`, not confirmed as fabricated.

## Notes for the team

- **Schema v1.1 is the contract.** No shared schema file existed in the workspace, so
  `schema/decision_record.schema.json` is canonical. It matches C's brief: `request_id`,
  `route`, top-level `claims` with three-state verdicts, `repair.previous_text`, and a
  `metrics.cost_usd` breakdown. `verification` stays as an additive audit block.
- **Unchecked means unverified.** The default stub (or a broken verifier import) produces
  `status: unchecked`, not `verified`. Wire `TRUSTROUTER_VERIFIER=backend_b.verifier:verify`
  to get real verdicts. Claims are normalized and capped at 50.
- **Gateway counts are integers.** `supported_claims` / `unsupported_claims` are counts;
  `unclear` is only visible in the record's `claims` array.
- **Repair cap:** one Ultra repair and one re-verification, to bound spend.
- **Background durability:** fast-mode verification runs in-process; a restart can leave a
  record `provisional` with `verification.status = "pending"`. The UI should poll with a
  timeout.
- **Old records:** the schema moved from v1.0 to v1.1; the local dev DB was reset rather than
  migrated. Existing v1.0 records are not readable as v1.1.
- **Security:** `.env` is ignored at the repo root; no keys appear in code, samples, or logs.
  Request bodies are stored locally, so treat `backend/data/` as sensitive.

## Next: Backend C frontend

Backend A is contract-ready for the React/Vite app (CORS enabled, counts + four statuses,
full records, and mock payloads). The frontend will live in `/frontend`, read mocks shaped by
`schema/decision_record.schema.json`, and switch to this API through one base-URL setting.
