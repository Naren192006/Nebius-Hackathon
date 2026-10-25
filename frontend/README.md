# TrustRouter frontend (Backend C)

React + Vite + TypeScript-free JSX console for the TrustRouter gateway. It renders the
**v1.1 decision record** from Backend A and never talks to Nebius directly: the browser only
calls the gateway, so no model key ever reaches the bundle.

```bash
cd frontend
npm install
cp .env.example .env.local   # optional; defaults already point at the gateway
npm run dev                  # http://127.0.0.1:5173
```

## Surfaces

| Route | Purpose |
| --- | --- |
| `/` | Prompt, mode (fast/verified), pinned tier, answer card, claims + evidence, cost breakdown, routing sidebar |
| `/lab` | Same prompt pinned to Nano, Super, and Ultra; claim verdicts and cost/latency per tier |
| `/why` | Decision record browser: recent records from `GET /decisions`, routing evidence, economics, claims, repair diff, raw JSON |
| `/dashboard` | Benchmark comparison across strategies (accuracy, supported-claim rate, cost/1k, p95 latency, repair rate) |

## Contract (`schema_version: 1.1`)

The UI reads exactly the fields Backend A writes (`backend/schema/decision_record.schema.json`):

- `status`: `provisional | verified | repaired | unchecked` — plus `failed` for a recorded
  upstream error. `unchecked` means **verification unavailable**, not "no claims to check".
- `claims[]`: `{text, verdict: supported|unsupported|unclear, source_url, evidence_snippet}`
- `repair`: `{triggered, model, previous_text}` (one repair pass)
- `verification`: `{verifier, status: pending|complete|skipped, verification_ms, error}`
- `metrics`: `{routing_ms, total_ms, cost_usd: {base, verification, repair, total}}`
- `route`: `{model, model_id, confidence, reason, upgraded_for_confidence, features}`

The gateway block in `/v1/chat/completions` carries claim **counts only**
(`supported_claims`, `unsupported_claims`); full claims come from `GET /decisions/{id}`.

## Mock vs live

`VITE_USE_MOCK` decides where data comes from; everything else is identical code.

| Mode | Source |
| --- | --- |
| `VITE_USE_MOCK=true` (default) | `src/data/mock.js` fixtures, contract-shaped, deterministic |
| `VITE_USE_MOCK=false` | `POST /v1/chat/completions`, `GET /decisions`, `GET /decisions/{id}` at `VITE_API_BASE_URL` |

Mock fixtures cover one verified answer, one repaired answer with a before/after diff, one
provisional record that settles after ~2s (exercises polling), and one `unchecked` record.
The running gateway shows a **Mock fixtures** badge; live mode shows **Live API** and links to
`/docs`. The benchmark screen always reads the checked-in sample and labels it as such until
Backend B publishes `results.json`.

## Checks run

- `npm run build` — 615 modules, no errors.
- Mock mode: prompt → verified/repaired answer, fast mode provisional → polled verified,
  Lab three-tier run, record switching on `/why`, dashboard charts and table.
- Live mode (`TRUSTROUTER_MOCK_LLM=1` gateway on `:8000`): chat completion renders with
  `Unchecked` + "No verifier was available", `/why` lists stored records from SQLite,
  `/lab` pins all three tiers. With a real verifier configured those statuses become
  `verified`/`repaired`/`unchecked` per the record.

## Notes

- Copy inside the UI is deliberately careful: *verified* means claims were checked against
  retrieved sources, not that the answer is guaranteed true.
- The bundle is a single chunk (~694 kB raw / 207 kB gzip) because of `recharts`; code-splitting
  the dashboard is the obvious next trim.
