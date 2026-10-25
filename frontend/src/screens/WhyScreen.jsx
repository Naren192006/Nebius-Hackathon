import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getDecision, listDecisions } from "../api/client";
import { costFor, supportRate } from "../data/mock";
import { StatusBadge, TierBadge, statusLabel, statusMeta } from "../components/Badges";
import { ClaimList } from "../components/Claims";
import { DiffView } from "../components/DiffView";
import { Icon } from "../components/Icon";
import {
  ConfidenceMeter,
  CostBreakdown,
  formatMs,
  formatUsd,
  IdChip,
  Metric,
  MetricRow,
  SupportBar,
} from "../components/Metrics";
import { FeatureChips } from "../components/WhyPanel";

const SIGNALS = [
  { key: "code_hits", label: "Code", icon: "layers" },
  { key: "math_hits", label: "Math", icon: "gauge" },
  { key: "factual_hits", label: "Factual risk", icon: "search" },
  { key: "reasoning_hits", label: "Reasoning", icon: "spark" },
];

function SignalGrid({ features }) {
  return (
    <div className="signal-grid">
      {SIGNALS.map((signal) => {
        const hits = features?.[signal.key] || [];
        return (
          <div className={`signal-card ${hits.length ? "signal-hit" : ""}`} key={signal.key}>
            <span className="signal-icon"><Icon name={signal.icon} size={15} /></span>
            <div>
              <strong>{signal.label}</strong>
              <span>{hits.length ? hits.join(" · ") : "no signal"}</span>
            </div>
            <b>{hits.length}</b>
          </div>
        );
      })}
    </div>
  );
}

export default function WhyScreen() {
  const [params, setParams] = useSearchParams();
  const [summaries, setSummaries] = useState([]);
  const [record, setRecord] = useState(null);
  const [error, setError] = useState("");

  const selectedId = params.get("record") || summaries[0]?.decision_id;

  useEffect(() => {
    let alive = true;
    listDecisions(30)
      .then((rows) => {
        if (alive) setSummaries(rows);
      })
      .catch((err) => {
        if (alive) setError(err.message || "Could not load decision records");
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return undefined;
    let alive = true;
    getDecision(selectedId)
      .then((next) => {
        if (!alive) return;
        if (next) setRecord(next);
        else if (summaries.length) setParams({ record: summaries[0].decision_id }, { replace: true });
      })
      .catch((err) => {
        if (alive) setError(err.message || "Could not load that decision record");
      });
    return () => {
      alive = false;
    };
  }, [selectedId, summaries, setParams]);

  const ultraCost = useMemo(() => {
    if (!record?.answer) return null;
    return costFor("ultra", record.answer.tokens_in, record.answer.tokens_out);
  }, [record]);

  if (error) {
    return (
      <div className="page">
        <div className="panel empty-benchmark">
          <Icon name="alert" size={20} />
          <h2>Decision records unavailable</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!record) {
    return (
      <div className="page">
        <section className="panel empty-benchmark">
          <p className="eyebrow">Decision record</p>
          <h2>{summaries.length ? "Loading record…" : "No decisions yet"}</h2>
          <p>
            {summaries.length
              ? "Fetching the stored record from the gateway."
              : "Send a prompt from the Ask screen and its record will appear here."}
          </p>
          <Link className="button button-ghost button-small" to="/">
            <Icon name="arrowRight" size={14} className="flip" /> Back to Ask
          </Link>
        </section>
      </div>
    );
  }

  const base = record.metrics.cost_usd.base;
  const savings = ultraCost ? Math.round(((ultraCost - base) / ultraCost) * 100) : null;
  const rate = supportRate(record);

  return (
    <div className="page why-page">
      <section className="page-intro">
        <div>
          <p className="eyebrow">Decision record</p>
          <h1>Show your work.</h1>
          <p className="page-lede">
            Every answer keeps its routing evidence, claim verdicts, repair history, and full cost
            breakdown. This is the record behind the badge.
          </p>
        </div>
        <div className="intro-facts">
          <span><b>{record.route.features?.score?.toFixed(2) ?? "—"}</b> routing score</span>
          <span><b>{record.route.confidence.toFixed(2)}</b> confidence</span>
          <span><b>{rate ?? "—"}%</b> claims supported</span>
        </div>
      </section>

      <div className="why-layout">
        <aside className="record-list panel" aria-label="Decision records">
          <div className="record-list-head">
            <p className="eyebrow">Recent decisions</p>
            <span>{summaries.length} records</span>
          </div>
          {summaries.map((item) => (
            <button
              key={item.decision_id}
              type="button"
              className={`record-item ${item.decision_id === record.request_id ? "record-active" : ""}`}
              onClick={() => setParams({ record: item.decision_id })}
            >
              <span
                className={`record-dot status-${statusMeta(item.status).tone}`}
                aria-hidden="true"
              />
              <span className="record-copy">
                <strong>{item.question}</strong>
                <span>
                  <TierBadge tier={item.model_tier} size="sm" />
                  <span className={`record-status status-${statusMeta(item.status).tone}`}>
                    {statusLabel(item.status)}
                  </span>
                  <span className="record-conf">conf {item.confidence?.toFixed(2) ?? "—"}</span>
                </span>
              </span>
            </button>
          ))}
          <div className="record-list-foot">
            <Icon name="shield" size={14} />
            <span>Records are persisted in SQLite by the gateway.</span>
          </div>
        </aside>

        <div className="why-detail">
          <section className="decision-hero panel">
            <div className="decision-hero-top">
              <div>
                <div className="answer-badges">
                  <StatusBadge status={record.status} />
                  <TierBadge tier={record.route.model} />
                  <span className="answer-mode">{record.mode} mode</span>
                </div>
                <h2>{record.request.question}</h2>
              </div>
              <div className="decision-hero-actions">
                <IdChip id={record.request_id} />
                <button
                  type="button"
                  className="button button-ghost button-small"
                  onClick={() => navigator.clipboard?.writeText(record.request_id)}
                >
                  Copy ID
                </button>
              </div>
            </div>
            <MetricRow className="decision-metrics">
              <Metric label="Routed to" value={record.route.model.toUpperCase()} size="sm" />
              <Metric label="Confidence" value={record.route.confidence.toFixed(2)} size="sm" />
              <Metric label="Latency" value={formatMs(record.metrics.total_ms)} size="sm" />
              <Metric label="Total cost" value={formatUsd(record.metrics.cost_usd.total)} size="sm" />
            </MetricRow>
          </section>

          <div className="why-grid">
            <section className="panel detail-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Routing</p>
                  <h3>Why {record.route.model}?</h3>
                </div>
                <Icon name="route" size={18} />
              </div>
              <ConfidenceMeter
                confidence={record.route.confidence}
                upgraded={record.route.upgraded_for_confidence}
              />
              <p className="route-reason">{record.route.reason}</p>
              <FeatureChips features={record.route.features} />
              <SignalGrid features={record.route.features} />
              <div className="feature-stats">
                <span>{record.route.features?.char_len ?? 0} chars</span>
                <span>{record.route.features?.word_count ?? 0} words</span>
                <span>score {record.route.features?.score?.toFixed(2) ?? "—"}</span>
                <span>margin {record.route.features?.boundary_margin?.toFixed(2) ?? "—"}</span>
              </div>
            </section>

            <section className="panel detail-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Economics</p>
                  <h3>What it cost</h3>
                </div>
                <Icon name="coin" size={18} />
              </div>
              <CostBreakdown costs={record.metrics.cost_usd} />
              <MetricRow className="econ-metrics">
                <Metric label="Routing" value={formatMs(record.metrics.routing_ms)} size="sm" />
                <Metric label="Total" value={formatMs(record.metrics.total_ms)} size="sm" />
                <Metric
                  label="Tokens"
                  value={`${record.answer?.tokens_in ?? 0} / ${record.answer?.tokens_out ?? 0}`}
                  hint="in / out"
                  size="sm"
                />
              </MetricRow>
              {ultraCost != null && (
                <div className={`savings ${savings < 0 ? "savings-negative" : ""}`}>
                  <span className="savings-icon"><Icon name="coin" size={15} /></span>
                  <div>
                    <strong>
                      {savings >= 0 ? `${savings}% model-spend savings` : `${Math.abs(savings)}% over Ultra`}
                    </strong>
                    <span>
                      {formatUsd(base)} on the routed model vs {formatUsd(ultraCost)} on Ultra for
                      the same tokens. Verification and repair are shown above.
                    </span>
                  </div>
                </div>
              )}
              <div className="econ-note">
                <Icon name="alert" size={14} />
                <span>{record.metrics.cost_usd.repair > 0 ? "Repair spend is included in the total." : "No repair spend on this decision."}</span>
              </div>
            </section>
          </div>

          <section className="panel detail-card claims-detail">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Verification</p>
                <h3>Claims and sources</h3>
              </div>
              <div className="claims-head-right">
                <span className="verification-pill">
                  {record.verification.status} · {record.verification.verifier}
                </span>
                <SupportBar claims={record.claims} showLegend={false} />
              </div>
            </div>
            <ClaimList claims={record.claims} status={record.status} />
          </section>

          {record.repair && (
            <section className="panel detail-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Repair history</p>
                  <h3>Before and after</h3>
                </div>
                <span className="panel-step">one pass</span>
              </div>
              <DiffView record={record} />
            </section>
          )}

          <details className="raw-record panel">
            <summary>
              <span>Raw decision record</span>
              <span className="raw-meta">schema v{record.schema_version} · JSON</span>
            </summary>
            <pre>{JSON.stringify(record, null, 2)}</pre>
          </details>

          <div className="why-footer-actions">
            <Link className="button button-ghost" to="/">
              <Icon name="arrowRight" size={14} className="flip" /> Back to Ask
            </Link>
            <Link className="button button-ghost" to="/lab">
              Compare tiers in the Lab <Icon name="arrowRight" size={14} />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
