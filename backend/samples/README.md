# Sample payloads for Backend C

Four responses captured from the running gateway (`POST /v1/chat/completions`), one per
status, plus the full decision records for three of them.

| File | Request | Gateway status |
| --- | --- | --- |
| `provisional.json` | `mode=fast`, router picks the tier | `provisional` |
| `verified.json` | `mode=verified`, `model=super`, supporting demo verifier | `verified` |
| `repaired.json` | `mode=verified`, `model=nano`, demo verifier rejects a claim | `repaired` |
| `unchecked.json` | `mode=verified`, `model=nano`, stub verifier | `unchecked` |

Full records: `records/verified.json`, `records/repaired.json`, `records/unchecked.json`.
These follow `schema/decision_record.schema.json` v1.1 exactly and are what
`GET /decisions/{id}` returns.

## What is real vs offline

Everything is generated through the real FastAPI handlers, validation, SQLite writes, and
repair flow, but with `TRUSTROUTER_MOCK_LLM=1`:

- `choices[0].message.content` starts with `[mock:<tier>]`
- token counts are the mock's character estimate, and `cost_usd.base` / `repair` are `0`
- `cost_usd.verification` is set to `0.001` per verifier call in these samples so the
  breakdown is visible; tune with `TRUSTROUTER_VERIFY_COST_USD`
- `verified.json` and `repaired.json` use [`scripts/demo_verifier.py`](../scripts/demo_verifier.py),
  a demo-only heuristic with a placeholder source URL. It is **not** Backend B.
- `unchecked.json` leaves the default stub in place, so no verifier ran.

The gateway's claim counts are integers. `unclear` claims are not counted as supported or
unsupported; read the record's `claims` array for the third verdict.

## Refresh against the real stack

Once `NEBIUS_API_KEY` is in `backend/.env` and Backend B exposes its verifier:

```bash
TRUSTROUTER_VERIFIER=backend_b.verifier:verify python scripts/make_samples.py
```

Remove the `TRUSTROUTER_MOCK_LLM` override in `scripts/make_samples.py` first. The repaired
sample requires a verifier that actually rejects a claim, so B must be wired before it can
be regenerated live.
