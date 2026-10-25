import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { runLab } from "../api/client";
import { USE_MOCK } from "../config";
import { labPrompt, labRecords, supportRate } from "../data/mock";
import { StatusBadge, TierBadge } from "../components/Badges";
import { ClaimList } from "../components/Claims";
import { ErrorState, SkeletonAnswer } from "../components/Feedback";
import { Icon } from "../components/Icon";
import {
  formatMs,
  formatUsd,
  Metric,
  MetricRow,
  SupportBar,
} from "../components/Metrics";

const TIERS = ["nano", "super", "ultra"];

function LabColumn({ tier, record }) {
  if (!record) {
    return (
      <article className={`lab-column panel tier-${tier}`}>
        <div className="lab-column-head">
          <TierBadge tier={tier} />
        </div>
        <SkeletonAnswer />
      </article>
    );
  }
  const rate = supportRate(record);
  return (
    <article className={`lab-column panel tier-${tier}`}>
      <header className="lab-column-head">
        <div>
          <TierBadge tier={tier} />
          <p className="tier-blurb">
            {tier === "nano" && "Fastest and cheapest"}
            {tier === "super" && "Balanced middle tier"}
            {tier === "ultra" && "Accuracy reference"}
          </p>
        </div>
        <StatusBadge status={record.status} size="sm" />
      </header>

      <p className="lab-answer">{record.answer.text}</p>

      <SupportBar claims={record.claims} />
      <ClaimList claims={record.claims} status={record.status} compact />

      <MetricRow className="lab-metrics">
        <Metric label="Cost" value={formatUsd(record.metrics.cost_usd.total)} size="sm" />
        <Metric label="Latency" value={formatMs(record.metrics.total_ms)} size="sm" />
        <Metric label="Support" value={rate == null ? "—" : `${rate}%`} size="sm" />
      </MetricRow>

      <footer className="lab-foot">
        <span className="lab-repair">
          <Icon name={record.repair ? "spark" : "clock"} size={13} />
          {record.repair ? "Repair attempted" : "No repair needed"}
        </span>
        <Link className="text-link" to={`/why?record=${record.request_id}`}>
          Why this tier? <Icon name="chevron" size={13} />
        </Link>
      </footer>
    </article>
  );
}

export default function LabScreen() {
  const [prompt, setPrompt] = useState(labPrompt);
  const [results, setResults] = useState(
    USE_MOCK
      ? {
          nano: structuredClone(labRecords.nano),
          super: structuredClone(labRecords.super),
          ultra: structuredClone(labRecords.ultra),
        }
      : null
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function run() {
    if (loading || !prompt.trim()) return;
    setLoading(true);
    setError("");
    try {
      const output = await runLab(prompt.trim());
      setResults(output);
    } catch (err) {
      setError(err.message || "Lab run failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!USE_MOCK && !results && !loading) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const records = results ? TIERS.map((tier) => results[tier]) : [];
  const totalCost = records.reduce(
    (sum, record) => sum + (record?.metrics.cost_usd.total || 0),
    0
  );
  const slowest = Math.max(0, ...records.map((record) => record?.metrics.total_ms || 0));

  return (
    <div className="page lab-page">
      <section className="page-intro">
        <div>
          <p className="eyebrow">Demo hook</p>
          <h1>One prompt. Three model sizes. Every claim checked.</h1>
          <p className="page-lede">
            The same question is pinned to Nano, Super, and Ultra. Green claims were sourced;
            amber ones were unclear; red ones failed verification.
          </p>
        </div>
        <div className="intro-facts">
          <span><b>{formatUsd(totalCost)}</b> total spend</span>
          <span><b>{formatMs(slowest)}</b> slowest tier</span>
          <span><b>3</b> parallel calls</span>
        </div>
      </section>

      <section className="lab-controls panel">
        <label htmlFor="lab-prompt" className="lab-label">
          Prompt across tiers
        </label>
        <div className="lab-input-row">
          <input
            id="lab-prompt"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && run()}
            spellCheck="false"
          />
          <button
            type="button"
            className="button button-primary"
            onClick={run}
            disabled={loading || !prompt.trim()}
          >
            {loading ? "Running…" : "Run across tiers"}
            {!loading && <Icon name="arrowRight" size={15} />}
          </button>
        </div>
        <p className="lab-note">
          {USE_MOCK
            ? "Demo fixtures are keyed to this prompt. Live mode answers any prompt and polls all three decisions."
            : "The Lab sends three verified-mode requests in parallel, then polls each decision until verification settles."}
        </p>
      </section>

      {error && <ErrorState message={error} onRetry={run} />}

      <div className="lab-grid">
        {TIERS.map((tier) => (
          <LabColumn key={tier} tier={tier} record={results?.[tier]} />
        ))}
      </div>

      <section className="lab-summary panel">
        <div className="lab-summary-copy">
          <Icon name="gauge" size={18} />
          <div>
            <strong>The trade-off is visible, not hidden.</strong>
            <span>
              Support rate rises with model size, and so do cost and latency. TrustRouter exists
              to buy the second without paying the third every time.
            </span>
          </div>
        </div>
        <div className="lab-summary-stats">
          {TIERS.map((tier) => (
            <span key={tier} className={`summary-stat tier-${tier}`}>
              <TierBadge tier={tier} size="sm" />
              {results?.[tier] ? `${supportRate(results[tier]) ?? "—"}%` : "—"}
            </span>
          ))}
        </div>
      </section>
    </div>
  );
}
