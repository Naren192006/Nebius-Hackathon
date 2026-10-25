export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

// Mock fixtures make the demo deterministic and keep the UI independent of the
// backend's mock model. Set VITE_USE_MOCK=false to talk to FastAPI.
export const USE_MOCK = (import.meta.env.VITE_USE_MOCK ?? "true") !== "false";

export const POLL_INTERVAL_MS = 700;
export const POLL_TIMEOUT_MS = 20000;
