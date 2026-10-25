import { countClaims, supportRate } from "../data/mock";

export const formatUsd = (value) => {
  if (value == null || Number.isNaN(value)) return "—";
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(5)}`;
  return `$${value.toFixed(3)}`;
};

export const formatMs = (value) =>
  value == null ? "—" : `${Math.round(value).toLocaleString()} ms`;

export const formatPct = (value, digits = 0) =>
  value == null ? "—" : `${value.toFixed(digits)}%`;

export function Metric({ label, value, hint, accent = "", size = "md" }) {
  return (
    <div className={`metric metric-${size} ${accent ? `metric-${accent}` : ""}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
      {hint && <span className="metric-hint">{hint}</span>}
    </div>
  );
}

export function MetricRow({ children, className = "" }) {
  return <div className={`metric-row ${className}`}>{children}</div>;
}

export function ConfidenceMeter({ confidence = 0, upgraded = false }) {
  const pct = Math.max(0, Math.min(1, confidence)) * 100;
  return (
    <div className="confidence">
      <div className="confidence-head">
        <span className="eyebrow">Routing confidence</span>
        <strong className={upgraded ? "text-amber" : ""}>{confidence.toFixed(2)}</strong>
      </div>
      <div
        className="confidence-track"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={confidence}
        aria-label="Routing confidence"
      >
        <span className="confidence-fill" style={{ width: `${pct}%` }} />
        <span className="confidence-threshold" style={{ left: "70%" }} />
      </div>
      <div className="confidence-legend">
        <span>0.00</span>
        <span className="confidence-threshold-label">0.70 escalate</span>
        <span>1.00</span>
      </div>
      {upgraded && (
        <p className="inline-note note-amber">
          Below 0.70, so the router moved up one tier before answering.
        </p>
      )}
    </div>
  );
}

export function CostBreakdown({ costs, compact = false }) {
  const total = costs?.total || 0;
  const parts = [
    { key: "base", label: "Model", value: costs?.base || 0 },
    { key: "verification", label: "Verify", value: costs?.verification || 0 },
    { key: "repair", label: "Repair", value: costs?.repair || 0 },
  ];
  return (
    <div className={`cost-breakdown ${compact ? "cost-compact" : ""}`}>
      <div className="cost-head">
        <span className="eyebrow">Cost breakdown</span>
        <strong>{formatUsd(total)}</strong>
      </div>
      {total > 0 ? (
        <div className="cost-track" aria-hidden="true">
          {parts.map((part) =>
            part.value > 0 ? (
              <span
                key={part.key}
                className={`cost-segment cost-${part.key}`}
                style={{ width: `${(part.value / total) * 100}%` }}
              />
            ) : null
          )}
        </div>
      ) : (
        <div className="cost-track cost-empty" />
      )}
      {!compact && (
        <div className="cost-legend">
          {parts.map((part) => (
            <span key={part.key} className="cost-legend-item">
              <i className={`cost-dot cost-${part.key}`} />
              {part.label}
              <b>{formatUsd(part.value)}</b>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function SupportBar({ claims = [], showLegend = true }) {
  const counts = countClaims(claims);
  const total = claims.length;
  const rate = supportRate({ claims });
  if (!total) {
    return (
      <div className="support-empty">
        <span className="support-empty-track" />
        <span>No claims checked</span>
      </div>
    );
  }
  const segments = [
    { key: "supported", value: counts.supported },
    { key: "unsupported", value: counts.unsupported },
    { key: "unclear", value: counts.unclear },
  ];
  return (
    <div className="support">
      <div className="support-head">
        <span className="eyebrow">Claim support</span>
        <strong>{rate}%</strong>
      </div>
      <div className="support-track" aria-hidden="true">
        {segments.map((segment) =>
          segment.value > 0 ? (
            <span
              key={segment.key}
              className={`support-segment support-${segment.key}`}
              style={{ width: `${(segment.value / total) * 100}%` }}
            />
          ) : null
        )}
      </div>
      {showLegend && (
        <div className="support-legend">
          <span><i className="support-dot support-supported" />{counts.supported} supported</span>
          <span><i className="support-dot support-unsupported" />{counts.unsupported} unsupported</span>
          <span><i className="support-dot support-unclear" />{counts.unclear} unclear</span>
        </div>
      )}
    </div>
  );
}

export function IdChip({ id }) {
  return (
    <span className="id-chip" title={id}>
      {id}
    </span>
  );
}
