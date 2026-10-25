import { TIER_META } from "../data/mock";
import { Icon } from "./Icon";

// The status vocabulary lives here so no screen has to render a raw key. `tone`
// is the CSS class suffix (see .status-* in styles.css), never the snake_case
// status itself.
export const STATUS_META = {
  verified: {
    label: "Verified",
    tone: "verified",
    hint: "Every claim was checked against sources.",
  },
  repaired: {
    label: "Repaired",
    tone: "repaired",
    hint: "A failed claim was rewritten, then re-checked.",
  },
  unchecked: {
    label: "Not verified",
    tone: "unchecked",
    hint: "The answer could not be confirmed.",
  },
  nothing_to_check: {
    label: "Nothing to check",
    tone: "nothing-to-check",
    hint: "Short reply with no claims to check.",
  },
  provisional: {
    label: "Verifying...",
    tone: "provisional",
    hint: "Verification is still running.",
  },
  failed: {
    label: "Failed",
    tone: "failed",
    hint: "The model call did not complete.",
  },
};

const UNKNOWN_META = {
  label: "Unknown state",
  tone: "unknown",
  hint: "This status has no meaning here.",
};

export function statusMeta(status) {
  return STATUS_META[status] || UNKNOWN_META;
}

export function statusLabel(status) {
  return statusMeta(status).label;
}

export function StatusBadge({ status, size = "md" }) {
  const meta = statusMeta(status);
  return (
    <span className={`status-stack status-stack-${size}`}>
      <span
        className={`status-badge status-${meta.tone} status-${size}`}
        title={meta.hint}
      >
        <span className="status-dot" aria-hidden="true" />
        {meta.label}
      </span>
      <span className="status-hint">{meta.hint}</span>
    </span>
  );
}

export function TierBadge({ tier, size = "md", withPrice = false }) {
  const meta = TIER_META[tier] || { label: tier, blurb: "", price: "" };
  return (
    <span className={`tier-badge tier-${tier} tier-${size}`} title={meta.blurb}>
      <span className="tier-mark" aria-hidden="true" />
      {meta.label}
      {withPrice && <span className="tier-price">${meta.price}/1M</span>}
    </span>
  );
}

export function VerdictBadge({ verdict, compact = false }) {
  const meta = {
    supported: { label: "Supported", icon: "check" },
    unsupported: { label: "Unsupported", icon: "x" },
    unclear: { label: "Unclear", icon: "help" },
  }[verdict] || { label: "Unclear", icon: "help" };
  return (
    <span className={`verdict verdict-${verdict} ${compact ? "verdict-compact" : ""}`}>
      <Icon name={meta.icon} size={compact ? 12 : 13} />
      {!compact && meta.label}
    </span>
  );
}

export function MockBadge({ live }) {
  return (
    <span className={`env-badge ${live ? "env-live" : "env-mock"}`}>
      <span className="env-dot" aria-hidden="true" />
      {live ? "Live API" : "Mock fixtures"}
    </span>
  );
}
