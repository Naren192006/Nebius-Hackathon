import { Link } from "react-router-dom";
import { costFor, countClaims } from "../data/mock";
import { TierBadge } from "./Badges";
import { Icon } from "./Icon";
import {
  ConfidenceMeter,
  CostBreakdown,
  formatMs,
  formatUsd,
  Metric,
  MetricRow,
} from "./Metrics";

const FEATURES = [
  { key: "code", label: "Code" },
  { key: "math", label: "Math" },
  { key: "factual_risk", label: "Factual risk" },
  { key: "reasoning", label: "Reasoning" },
];

export function FeatureChips({ features = {} }) {
  return (
    <div className="feature-chips">
      {FEATURES.map((feature) => {
        const active = Boolean(features[feature.key]);
        return (
          <span
            key={feature.key}
            className={`feature-chip ${active ? "feature-on" : "feature-off"}`}
          >
            <span className="feature-dot" aria-hidden="true" />
            {feature.label}
          </span>
        );
      })}
    </div>
  );
}

function savingsFor(record) {
  const answer = record?.answer;
  if (!answer) return null;
  const ultra = costFor("ultra", answer.tokens_in, answer.tokens_out);
  const base = record.metrics.cost_usd.base;
  if (!ultra) return null;
  return {
    ultra,
    base,
    percent: Math.round(((ultra - base) / ultra) * 100),
  };
}

export function WhyPanel({ record }) {
  if (!record) return <RoutePrimer />;
  const counts = countClaims(record.claims);
  const savings = savingsFor(record);

  return (
    <aside className="why-panel panel" aria-label="Why this model was chosen">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Why this model?</p>
          <h3>
            <TierBadge tier={record.route.model} /> <span className="muted">chosen</span>
          </h3>
        </div>
        <span className="panel-step">Route</span>
      </div>

      <ConfidenceMeter
        confidence={record.route.confidence}
        upgraded={record.route.upgraded_for_confidence}
      />

      <p className="route-reason">{record.route.reason}</p>

      <FeatureChips features={record.route.features} />

      <MetricRow className="why-metrics">
        <Metric label="Latency" value={formatMs(record.metrics.total_ms)} size="sm" />
        <Metric label="Cost" value={formatUsd(record.metrics.cost_usd.total)} size="sm" />
        <Metric
          label="Claims"
          value={`${counts.supported}/${record.claims.length || 0}`}
          hint="supported"
          size="sm"
        />
      </MetricRow>

      <CostBreakdown costs={record.metrics.cost_usd} compact />

      {savings && (
        <div className={`savings ${savings.percent < 0 ? "savings-negative" : ""}`}>
          <span className="savings-icon"><Icon name="coin" size={15} /></span>
          <div>
            <strong>
              {savings.percent >= 0 ? `${savings.percent}% model-spend savings` : `${Math.abs(savings.percent)}% over Ultra`}
            </strong>
            <span>
              {formatUsd(savings.base)} vs {formatUsd(savings.ultra)} on Ultra for the same tokens
            </span>
          </div>
        </div>
      )}

      <Link className="text-link why-link" to={`/why?record=${record.request_id}`}>
        Open full decision record <Icon name="chevron" size={14} />
      </Link>
    </aside>
  );
}

export function RoutePrimer() {
  return (
    <aside className="why-panel panel why-primer" aria-label="How routing works">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">How the router decides</p>
          <h3>Cheap first, escalation when unsure</h3>
        </div>
        <span className="panel-step">0.70</span>
      </div>

      <div className="route-ladder" aria-hidden="true">
        <div className="ladder-tier ladder-nano">
          <span>Nano</span>
          <small>score &lt; 1.0</small>
        </div>
        <div className="ladder-tier ladder-super">
          <span>Super</span>
          <small>score &lt; 3.0</small>
        </div>
        <div className="ladder-tier ladder-ultra">
          <span>Ultra</span>
          <small>score ≥ 3.0</small>
        </div>
      </div>

      <p className="route-reason">
        Length, code, math, factual-risk, and reasoning signals produce a score. If confidence
        lands below <b>0.70</b>, the router moves up one tier.
      </p>

      <div className="signal-list">
        <span><Icon name="layers" size={14} /> Code &amp; math</span>
        <span><Icon name="search" size={14} /> Dates, citations, “latest”</span>
        <span><Icon name="spark" size={14} /> Reasoning &amp; trade-offs</span>
      </div>

      <div className="primer-foot">
        <Icon name="shield" size={15} />
        <span>Verified means claims were checked, not that truth is guaranteed.</span>
      </div>
    </aside>
  );
}
