import { API_BASE_URL, POLL_INTERVAL_MS, POLL_TIMEOUT_MS, USE_MOCK } from "../config";
import { countClaims, labRecords, mockRecords, pickMockRecord, recordById, toChatResponse } from "../data/mock";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Fast-mode mock sessions settle after a short delay so the UI can demonstrate
// provisional -> verified polling without a backend.
const sessions = new Map();

function toProvisional(record) {
  const next = structuredClone(record);
  next.status = "provisional";
  next.answer.status = "provisional";
  next.claims = [];
  next.repair = null;
  next.verification = {
    verifier: "tavily",
    status: "pending",
    verification_ms: null,
    error: null,
  };
  next.metrics = {
    ...next.metrics,
    cost_usd: {
      base: next.metrics.cost_usd.base,
      verification: 0,
      repair: 0,
      total: next.metrics.cost_usd.base,
    },
  };
  return next;
}

async function parseError(response) {
  let message = `Gateway returned ${response.status}`;
  try {
    const body = await response.json();
    message = body?.error?.message || message;
  } catch {
    // keep the status message
  }
  throw new Error(message);
}

async function fetchDecision(decisionId) {
  const response = await fetch(`${API_BASE_URL}/decisions/${decisionId}`);
  if (!response.ok) await parseError(response);
  return response.json();
}

function toSummary(record) {
  const counts = countClaims(record.claims);
  return {
    decision_id: record.request_id,
    request_id: record.request_id,
    created_at: record.created_at,
    status: record.status,
    mode: record.mode,
    question: record.request.question,
    model_tier: record.route.model,
    confidence: record.route.confidence,
    cost_usd: record.metrics.cost_usd.total,
    supported_claims: counts.supported,
    unsupported_claims: counts.unsupported,
    unclear_claims: counts.unclear,
  };
}

export async function listDecisions(limit = 30) {
  if (USE_MOCK) {
    return mockRecords.map(toSummary);
  }
  const response = await fetch(`${API_BASE_URL}/decisions?limit=${limit}`);
  if (!response.ok) await parseError(response);
  const body = await response.json();
  return body.data || [];
}

export async function getDecision(decisionId) {
  if (USE_MOCK) {
    const session = sessions.get(decisionId);
    if (session) {
      return Date.now() >= session.readyAt ? session.settled : session.provisional;
    }
    return recordById(decisionId) || null;
  }
  return fetchDecision(decisionId);
}

export async function pollDecision(decisionId, onUpdate) {
  let record = await getDecision(decisionId);
  const started = Date.now();
  while (record && record.status === "provisional" && Date.now() - started < POLL_TIMEOUT_MS) {
    await delay(POLL_INTERVAL_MS);
    record = await getDecision(decisionId);
    onUpdate?.(record);
  }
  return record;
}

export async function submitPrompt({ prompt, mode = "verified", model = "auto", onStage }) {
  onStage?.("routing");

  if (USE_MOCK) {
    await delay(280);
    onStage?.("answering");
    await delay(420);
    const settled = pickMockRecord(prompt, mode, model);
    if (mode === "fast") {
      onStage?.("verifying");
      const provisional = toProvisional(settled);
      sessions.set(provisional.request_id, {
        provisional,
        settled,
        readyAt: Date.now() + 1900,
      });
      return { response: toChatResponse(provisional), record: provisional };
    }
    onStage?.("verifying");
    await delay(520);
    return { response: toChatResponse(settled), record: settled };
  }

  const response = await fetch(`${API_BASE_URL}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      mode,
      max_tokens: 512,
      messages: [{ role: "user", content: prompt }],
    }),
  });
  if (!response.ok) await parseError(response);
  const body = await response.json();
  const decisionId = body.gateway?.decision_id;
  if (!decisionId) throw new Error("Gateway response is missing gateway.decision_id");

  const record = await fetchDecision(decisionId);
  return { response: body, record };
}

export async function runLab(prompt) {
  if (USE_MOCK) {
    await delay(900);
    return {
      nano: structuredClone(labRecords.nano),
      super: structuredClone(labRecords.super),
      ultra: structuredClone(labRecords.ultra),
    };
  }

  const entries = await Promise.all(
    ["nano", "super", "ultra"].map(async (tier) => {
      const { record } = await submitPrompt({ prompt, mode: "verified", model: tier });
      const settled = record.status === "provisional" ? await pollDecision(record.request_id) : record;
      return [tier, settled];
    })
  );
  return Object.fromEntries(entries);
}

export async function getBenchmark() {
  // The dashboard reads the exported benchmark straight from the public file.
  const response = await fetch(`${import.meta.env.BASE_URL}results.json`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`results.json returned ${response.status}`);
  }
  return response.json();
}
