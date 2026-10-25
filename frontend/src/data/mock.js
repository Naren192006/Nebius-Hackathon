// Contract-shaped fixtures for the demo. Every object follows
// backend/schema/decision_record.schema.json v1.1. Swap VITE_USE_MOCK=false to
// use the live gateway instead.

export const MODELS = {
  nano: "nvidia/nvidia-nemotron-3-nano-30b-a3b",
  super: "nvidia/nemotron-3-super-120b-a12b",
  ultra: "nvidia/Nemotron-3-Ultra-550b-a55b",
};

export const TIER_META = {
  nano: {
    label: "Nano",
    blurb: "Fast, cheap, best for simple work",
    price: "0.06 / 0.24",
  },
  super: {
    label: "Super",
    blurb: "Balanced reasoning and cost",
    price: "0.30 / 0.90",
  },
  ultra: {
    label: "Ultra",
    blurb: "Frontier accuracy, highest cost",
    price: "1.00 / 3.00",
  },
};

export const PRICES = {
  nano: { in: 0.06, out: 0.24 },
  super: { in: 0.3, out: 0.9 },
  ultra: { in: 1, out: 3 },
};

export const VERIFY_COST = 0.0011;

const round = (value) => Math.round(value * 1e8) / 1e8;

export function costFor(tier, tokensIn, tokensOut) {
  const price = PRICES[tier] || PRICES.nano;
  return round((tokensIn * price.in + tokensOut * price.out) / 1_000_000);
}

export function countClaims(claims = []) {
  return claims.reduce(
    (acc, claim) => {
      const verdict = claim.verdict || "unclear";
      acc[verdict] = (acc[verdict] || 0) + 1;
      return acc;
    },
    { supported: 0, unsupported: 0, unclear: 0 }
  );
}

export function supportRate(record) {
  const claims = record?.claims || [];
  if (!claims.length) return null;
  const counts = countClaims(claims);
  return Math.round((counts.supported / claims.length) * 100);
}

export const claim = (text, verdict, source_url = null, evidence_snippet = null) => ({
  text,
  verdict,
  source_url,
  evidence_snippet,
});

function featureBlock(question, overrides = {}) {
  const words = question.trim().split(/\s+/).filter(Boolean).length;
  return {
    char_len: question.length,
    word_count: words,
    estimated_tokens: Math.max(1, Math.round(question.length / 4)),
    code: false,
    math: false,
    factual_risk: false,
    reasoning: false,
    multi_part: false,
    code_hits: [],
    math_hits: [],
    factual_hits: [],
    reasoning_hits: [],
    signal_count: 0,
    score: 0,
    boundary_margin: 1.5,
    base_tier: "nano",
    escalated: false,
    ...overrides,
  };
}

function makeRecord({
  id,
  question,
  mode,
  status,
  tier,
  baseTier,
  confidence,
  reason,
  features,
  answer,
  claims = [],
  repair = null,
  routing_ms = 0.07,
  total_ms,
  baseTokens,
  verificationCalls = 0,
  verificationMs = null,
  verifier = "tavily",
  verificationStatus = "complete",
  error = null,
  createdAt = "2026-10-21T16:24:08.412Z",
  requested = "auto",
}) {
  const base = costFor(baseTier || tier, baseTokens[0], baseTokens[1]);
  const repairCost = repair?.tokens ? costFor("ultra", repair.tokens[0], repair.tokens[1]) : 0;
  const verification = round(verificationCalls * VERIFY_COST);
  const total = round(base + verification + repairCost);
  return {
    schema_version: "1.1",
    request_id: id,
    created_at: createdAt,
    updated_at: createdAt,
    mode,
    status,
    request: {
      question,
      model_requested: requested,
      messages: [{ role: "user", content: question }],
      max_tokens: 512,
      temperature: 0.2,
    },
    route: {
      model: tier,
      model_id: MODELS[tier],
      confidence,
      reason,
      upgraded_for_confidence: Boolean(features?.escalated),
      features: featureBlock(question, { base_tier: baseTier || tier, ...features }),
    },
    answer: {
      status,
      text: answer.text,
      model: MODELS[answer.tier],
      model_tier: answer.tier,
      tokens_in: answer.tokens[0],
      tokens_out: answer.tokens[1],
      latency_ms: answer.latency,
      finish_reason: "stop",
      mock: false,
    },
    claims,
    repair: repair
      ? { triggered: true, model: MODELS.ultra, previous_text: repair.previous_text }
      : null,
    metrics: {
      routing_ms,
      total_ms,
      cost_usd: { base, verification, repair: repairCost, total },
    },
    verification: {
      verifier,
      status: verificationStatus,
      verification_ms: verificationStatus === "complete" ? verificationMs : null,
      error: verificationStatus === "complete" ? null : error,
    },
    error,
  };
}

const BELOVED_QUESTION = "Who wrote the novel Beloved?";

export const verifiedRecord = makeRecord({
  id: "dec_7a1f2c9d4b8e6a3f0c5d2e11",
  question: BELOVED_QUESTION,
  mode: "verified",
  status: "verified",
  tier: "super",
  baseTier: "super",
  confidence: 0.84,
  reason:
    "super: factual-risk signal (who wrote) lifted the score above the Nano threshold; confidence 0.84 is above the 0.70 escalation floor.",
  features: {
    factual_risk: true,
    factual_hits: ["authorship:who wrote"],
    signal_count: 1,
    score: 1.25,
    boundary_margin: 0.25,
  },
  answer: {
    tier: "super",
    text:
      "Beloved was written by Toni Morrison. It was published in 1987 and won the Pulitzer Prize for Fiction in 1988.",
    tokens: [14, 38],
    latency: 1210,
  },
  baseTokens: [14, 38],
  claims: [
    claim(
      "Beloved was written by Toni Morrison.",
      "supported",
      "https://www.nobelprize.org/prizes/literature/1993/morrison/facts/",
      "Toni Morrison received the 1993 Nobel Prize in Literature; Beloved is among her best-known novels."
    ),
    claim(
      "It was published in 1987.",
      "supported",
      "https://www.britannica.com/topic/Beloved-novel-by-Morrison",
      "Beloved, novel by Toni Morrison, published in 1987."
    ),
    claim(
      "It won the Pulitzer Prize for Fiction in 1988.",
      "supported",
      "https://www.pulitzer.org/winners/toni-morrison",
      "Toni Morrison won the 1988 Pulitzer Prize for Fiction for Beloved."
    ),
  ],
  total_ms: 1742,
  verificationCalls: 1,
  verificationMs: 412,
});

const REYKJAVIK_QUESTION = "What was Reykjavík's population in 2024?";

export const repairedRecord = makeRecord({
  id: "dec_9b3e5d7c1a2f4e6b8c0d3a22",
  question: REYKJAVIK_QUESTION,
  mode: "verified",
  status: "repaired",
  tier: "super",
  baseTier: "super",
  confidence: 0.75,
  reason:
    "super: date and factual-lookup signals raised the stakes; confidence 0.75 stayed above the escalation floor, so the first answer used Super.",
  features: {
    factual_risk: true,
    factual_hits: ["date:2024", "fact lookup:population"],
    signal_count: 2,
    score: 2.5,
    boundary_margin: 0.5,
  },
  answer: {
    tier: "ultra",
    text:
      "Reykjavík had a population of about 139,000 in 2024. The figure is a population estimate rather than a census count.",
    tokens: [19, 44],
    latency: 1980,
  },
  baseTokens: [9, 24],
  claims: [
    claim(
      "Reykjavík had a population of about 139,000 in 2024.",
      "supported",
      "https://www.statice.is/statistics/population/inhabitants/",
      "Statistics Iceland publishes the capital region population; the 2024 estimate is approximately 139,000."
    ),
    claim(
      "The figure is a population estimate rather than a census count.",
      "supported",
      "https://www.statice.is/statistics/population/inhabitants/",
      "Population figures are updated from the national register rather than a full census."
    ),
  ],
  repair: {
    previous_text:
      "Reykjavík had a population of 142,318 in 2024, according to Statistics Iceland.",
  },
  total_ms: 3320,
  verificationCalls: 2,
  verificationMs: 1080,
});

export const provisionalRecord = makeRecord({
  id: "dec_4c2a8e6f1b9d3c5a7e0f2b33",
  question: "Summarize what an API gateway does in one sentence.",
  mode: "fast",
  status: "provisional",
  tier: "nano",
  baseTier: "nano",
  confidence: 0.8,
  reason:
    "nano: short, signal-free prompt with no code, math, factual, or reasoning markers.",
  features: { score: -0.5, boundary_margin: 1.5 },
  answer: {
    tier: "nano",
    text:
      "An API gateway sits in front of backend services, routing requests and enforcing shared policy such as authentication, rate limits, and logging.",
    tokens: [14, 28],
    latency: 410,
  },
  baseTokens: [14, 28],
  claims: [],
  total_ms: 430,
  verificationStatus: "pending",
  requested: "auto",
});

export const uncheckedRecord = makeRecord({
  id: "dec_6d4b2a8c0e1f3a5b7c9d4e44",
  question: "How many moons does Neptune have?",
  mode: "verified",
  status: "unchecked",
  tier: "super",
  baseTier: "super",
  confidence: 0.76,
  reason:
    "super: factual-lookup signal (how many) and a short factual question; confidence 0.76 stayed on Super.",
  features: {
    factual_risk: true,
    factual_hits: ["fact lookup:how many"],
    signal_count: 1,
    score: 1.25,
    boundary_margin: 0.25,
  },
  answer: {
    tier: "super",
    text: "Neptune has 16 known moons.",
    tokens: [8, 10],
    latency: 640,
  },
  baseTokens: [8, 10],
  claims: [],
  total_ms: 660,
  verificationStatus: "skipped",
  verifier: "stub",
  verificationMs: null,
  error: null,
});

const CONCORDE_QUESTION =
  "What year did the last commercial Concorde flight take place, and on which route?";

const concordeFeatures = {
  factual_risk: true,
  multi_part: true,
  factual_hits: ["fact lookup:what year"],
  signal_count: 1,
  score: 1.65,
  boundary_margin: 0.35,
};

export const labNano = makeRecord({
  id: "dec_1e2f3a4b5c6d7e8f9a0b1c55",
  question: CONCORDE_QUESTION,
  mode: "verified",
  status: "repaired",
  tier: "nano",
  baseTier: "super",
  confidence: 1,
  reason:
    "Explicit model 'nano'. The router would have chosen Super (score 1.65); the Lab pins Nano to expose its raw reliability.",
  features: concordeFeatures,
  answer: {
    tier: "nano",
    text:
      "Concorde stopped flying in the 1990s after a crash in Paris.",
    tokens: [12, 26],
    latency: 380,
  },
  baseTokens: [12, 26],
  claims: [
    claim("Concorde stopped flying in the 1990s.", "unsupported"),
    claim("A crash in Paris ended the programme.", "unsupported"),
  ],
  repair: {
    previous_text:
      "The last commercial Concorde flight was in 2001, flying from London to Paris.",
  },
  total_ms: 980,
  verificationCalls: 2,
  verificationMs: 510,
  requested: "nano",
});

export const labSuper = makeRecord({
  id: "dec_2f3a4b5c6d7e8f9a0b1c2d66",
  question: CONCORDE_QUESTION,
  mode: "verified",
  status: "verified",
  tier: "super",
  baseTier: "super",
  confidence: 1,
  reason:
    "Explicit model 'super'. The router also chose Super (score 1.65), so the Lab isolates model size from routing.",
  features: concordeFeatures,
  answer: {
    tier: "super",
    text:
      "The last commercial Concorde flight was on 24 October 2003, from London Heathrow to New York JFK.",
    tokens: [15, 30],
    latency: 940,
  },
  baseTokens: [15, 30],
  claims: [
    claim(
      "The last commercial Concorde flight was on 24 October 2003.",
      "supported",
      "https://en.wikipedia.org/wiki/Concorde",
      "Concorde's last commercial flight was BA002 from New York to London on 24 October 2003."
    ),
    claim(
      "It flew from London Heathrow to New York JFK.",
      "unclear",
      null,
      "The direction could not be confirmed from the retrieved sources."
    ),
  ],
  total_ms: 1510,
  verificationCalls: 1,
  verificationMs: 420,
  requested: "super",
});

export const labUltra = makeRecord({
  id: "dec_3a4b5c6d7e8f9a0b1c2d3e77",
  question: CONCORDE_QUESTION,
  mode: "verified",
  status: "verified",
  tier: "ultra",
  baseTier: "super",
  confidence: 1,
  reason:
    "Explicit model 'ultra'. The router would have chosen Super (score 1.65); the Lab pins Ultra as the accuracy reference.",
  features: concordeFeatures,
  answer: {
    tier: "ultra",
    text:
      "The last commercial Concorde flight was on 24 October 2003, from New York JFK to London Heathrow.",
    tokens: [15, 30],
    latency: 2260,
  },
  baseTokens: [15, 30],
  claims: [
    claim(
      "The last commercial Concorde flight was on 24 October 2003.",
      "supported",
      "https://en.wikipedia.org/wiki/Concorde",
      "The final passenger-carrying flight was BA002 from New York JFK to London Heathrow."
    ),
    claim(
      "It flew from New York JFK to London Heathrow.",
      "supported",
      "https://www.britannica.com/technology/Concorde",
      "Concorde's last commercial service carried passengers from New York to London."
    ),
  ],
  total_ms: 2840,
  verificationCalls: 1,
  verificationMs: 390,
  requested: "ultra",
});

export const labRecords = { nano: labNano, super: labSuper, ultra: labUltra };

export const mockRecords = [
  verifiedRecord,
  repairedRecord,
  provisionalRecord,
  uncheckedRecord,
  labNano,
  labSuper,
  labUltra,
];

export const recordById = (id) => mockRecords.find((record) => record.request_id === id);

export const labPrompt = CONCORDE_QUESTION;

export const promptSuggestions = [
  { label: "Grounded fact", prompt: BELOVED_QUESTION },
  { label: "Needs repair", prompt: REYKJAVIK_QUESTION },
  { label: "No verifier", prompt: "How many moons does Neptune have?" },
  { label: "Fast answer", prompt: "Summarize what an API gateway does in one sentence." },
];

export function pickMockRecord(prompt, mode, model) {
  const text = (prompt || "").toLowerCase();
  let base = verifiedRecord;
  if (text.includes("reykjav") || text.includes("population")) base = repairedRecord;
  else if (text.includes("neptune") || text.includes("moons")) base = uncheckedRecord;
  else if (text.includes("api gateway")) base = provisionalRecord;

  const record = structuredClone(base);
  if (model && model !== "auto") {
    record.route = {
      ...record.route,
      model,
      model_id: MODELS[model],
      confidence: 1,
      upgraded_for_confidence: false,
      reason: `Explicit model '${model}' from the request; the Lab pins a tier instead of routing.`,
    };
    record.answer.model = MODELS[model];
    record.answer.model_tier = model;
    record.request.model_requested = model;
  }
  return record;
}

export function toChatResponse(record) {
  const counts = countClaims(record.claims);
  return {
    id: `chatcmpl-${record.request_id.slice(4)}`,
    object: "chat.completion",
    created: Math.floor(Date.parse(record.created_at) / 1000),
    model: record.route.model_id,
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: record.answer?.text || "" },
        finish_reason: "stop",
      },
    ],
    usage: {
      prompt_tokens: record.answer?.tokens_in || 0,
      completion_tokens: record.answer?.tokens_out || 0,
      total_tokens: (record.answer?.tokens_in || 0) + (record.answer?.tokens_out || 0),
    },
    gateway: {
      status: record.status,
      model_tier: record.route.model,
      decision_id: record.request_id,
      supported_claims: counts.supported,
      unsupported_claims: counts.unsupported,
      cost_usd: record.metrics.cost_usd.total,
    },
  };
}

// Benchmark numbers live in public/results.json only; there is no sample here.
export const priceFor = (tier) => PRICES[tier] || PRICES.nano;
