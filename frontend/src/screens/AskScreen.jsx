import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { pollDecision, submitPrompt } from "../api/client";
import { promptSuggestions } from "../data/mock";
import { ClaimList } from "../components/Claims";
import { DiffView } from "../components/DiffView";
import { EmptyAsk, ErrorState, SkeletonAnswer, StageBar } from "../components/Feedback";
import { Icon } from "../components/Icon";
import { StatusBadge, TierBadge } from "../components/Badges";
import {
  countClaims,
  supportRate,
} from "../data/mock";
import {
  CostBreakdown,
  formatMs,
  formatUsd,
  IdChip,
  Metric,
  MetricRow,
  SupportBar,
} from "../components/Metrics";
import { WhyPanel } from "../components/WhyPanel";

function AnswerCard({ record, stage, tab, setTab }) {
  const counts = countClaims(record.claims);
  const verification = record.verification;
  const verifiedLine =
    record.status === "unchecked"
      ? "Not checked · no verifier available"
      : record.status === "provisional"
        ? "Verification running · polling for claims"
        : `${verification.verifier} · ${record.claims.length} claims · ${formatMs(verification.verification_ms)}`;

  return (
    <article className="answer-card panel">
      <header className="answer-head">
        <div className="answer-title">
          <div className="answer-badges">
            <StatusBadge status={record.status} />
            <TierBadge tier={record.route.model} />
            <span className="answer-mode">{record.mode} mode</span>
          </div>
          <h2>{record.request.question}</h2>
        </div>
        <div className="answer-actions">
          <IdChip id={record.request_id} />
          <Link className="button button-ghost button-small" to={`/why?record=${record.request_id}`}>
            Why?
          </Link>
        </div>
      </header>

      {stage && <StageBar stage={stage} />}

      {record.repair && (
        <div className="repair-banner">
          <span className="repair-icon"><Icon name="spark" size={15} /></span>
          <div>
            <strong>Repaired with Ultra</strong>
            <span>
              A claim failed verification, so the answer was rewritten and checked again.
            </span>
          </div>
          <button type="button" className="repair-jump" onClick={() => setTab("diff")}>
            View diff <Icon name="chevron" size={12} />
          </button>
        </div>
      )}

      <MetricRow className="answer-metrics">
        <Metric label="First answer" value={formatMs(record.answer.latency_ms)} size="sm" />
        <Metric label="Total" value={formatMs(record.metrics.total_ms)} size="sm" />
        <Metric label="Cost" value={formatUsd(record.metrics.cost_usd.total)} size="sm" />
        <Metric
          label="Claims"
          value={`${counts.supported} supported`}
          hint={`${counts.unsupported} unsupported · ${counts.unclear} unclear`}
          size="sm"
        />
      </MetricRow>

      <div className="answer-tabs" role="tablist" aria-label="Answer views">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "answer"}
          className={`tab ${tab === "answer" ? "tab-active" : ""}`}
          onClick={() => setTab("answer")}
        >
          Answer
        </button>
        {record.repair && (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "diff"}
            className={`tab ${tab === "diff" ? "tab-active" : ""}`}
            onClick={() => setTab("diff")}
          >
            Before repair
          </button>
        )}
        <span className="tab-spacer" />
        <span className={`verification-line verification-${record.status}`}>
          {verifiedLine}
        </span>
      </div>

      {tab === "diff" && record.repair ? (
        <DiffView record={record} />
      ) : (
        <div className="answer-body">
          <p className="answer-text">{record.answer.text}</p>
          <div className="answer-cost">
            <CostBreakdown costs={record.metrics.cost_usd} compact />
          </div>
        </div>
      )}

      <section className="claims-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Verification</p>
            <h3>Claims and evidence</h3>
          </div>
          <SupportBar claims={record.claims} showLegend={false} />
        </div>
        <ClaimList claims={record.claims} status={record.status} />
        <div className="claims-legend">
          <span><i className="support-dot support-supported" />supported</span>
          <span><i className="support-dot support-unsupported" />unsupported</span>
          <span><i className="support-dot support-unclear" />unclear</span>
          <span className="claims-legend-note">
            support rate {supportRate(record) == null ? "—" : `${supportRate(record)}%`}
          </span>
        </div>
      </section>
    </article>
  );
}

export default function AskScreen() {
  const [prompt, setPrompt] = useState(promptSuggestions[0].prompt);
  const [mode, setMode] = useState("verified");
  const [model, setModel] = useState("auto");
  const [stage, setStage] = useState(null);
  const [record, setRecord] = useState(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("answer");
  const requestRef = useRef(0);

  const busy = Boolean(stage);

  async function run() {
    const text = prompt.trim();
    if (!text || busy) return;
    const requestId = ++requestRef.current;
    setError("");
    setRecord(null);
    setTab("answer");
    setStage("routing");

    try {
      const { record: initial } = await submitPrompt({
        prompt: text,
        mode,
        model,
        onStage: (next) => {
          if (requestRef.current === requestId) setStage(next);
        },
      });
      if (requestRef.current !== requestId) return;
      setRecord(initial);

      if (initial.status === "provisional") {
        setStage("verifying");
        const settled = await pollDecision(initial.request_id, (next) => {
          if (requestRef.current === requestId) setRecord(next);
        });
        if (requestRef.current === requestId) {
          setRecord(settled);
          setStage(null);
        }
      } else {
        setStage(null);
      }
    } catch (err) {
      if (requestRef.current !== requestId) return;
      setError(err.message || "Unknown gateway error");
      setStage(null);
    }
  }

  function onKeyDown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      run();
    }
  }

  return (
    <div className="page ask-page">
      <section className="page-intro ask-intro">
        <div>
          <p className="eyebrow">Gateway console</p>
          <h1>Ask once. Pay for what the question needs.</h1>
          <p className="page-lede">
            The router chooses a model tier, the verifier checks every claim, and failed answers
            get one Ultra repair before they reach you.
          </p>
        </div>
        <div className="intro-facts">
          <span><b>3</b> model tiers</span>
          <span><b>4</b> answer states</span>
          <span><b>0.70</b> escalation floor</span>
        </div>
      </section>

      <div className="ask-grid">
        <div className="ask-main">
          <section className="prompt-card panel">
            <div className="prompt-head">
              <label htmlFor="prompt">Prompt</label>
              <span className="char-count">{prompt.length} / 100,000</span>
            </div>
            <textarea
              id="prompt"
              rows={4}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask a question, paste a stack trace, or request an explanation…"
              spellCheck="false"
            />
            <div className="prompt-controls">
              <div className="segmented" role="group" aria-label="Verification mode">
                {[
                  { key: "fast", label: "Fast", hint: "answer first" },
                  { key: "verified", label: "Verified", hint: "wait for claims" },
                ].map((option) => (
                  <button
                    key={option.key}
                    type="button"
                    aria-pressed={mode === option.key}
                    className={`segment ${mode === option.key ? "segment-active" : ""}`}
                    onClick={() => setMode(option.key)}
                  >
                    <strong>{option.label}</strong>
                    <span>{option.hint}</span>
                  </button>
                ))}
              </div>

              <label className="model-select">
                <span>Model</span>
                <select value={model} onChange={(event) => setModel(event.target.value)}>
                  <option value="auto">Auto · route it</option>
                  <option value="nano">Nano · pin</option>
                  <option value="super">Super · pin</option>
                  <option value="ultra">Ultra · pin</option>
                </select>
              </label>

              <button
                type="button"
                className="button button-primary send-button"
                onClick={run}
                disabled={busy || !prompt.trim()}
              >
                {busy ? "Working…" : "Send prompt"}
                {!busy && <Icon name="arrowRight" size={15} />}
              </button>
            </div>
            <div className="prompt-foot">
              <span className="key-hint"><kbd>⌘</kbd>/<kbd>Ctrl</kbd> + <kbd>Enter</kbd> to send</span>
              <div className="suggestion-row">
                {promptSuggestions.map((item) => (
                  <button
                    key={item.label}
                    type="button"
                    className="suggestion-chip"
                    onClick={() => setPrompt(item.prompt)}
                    title={item.prompt}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          </section>

          {error && <ErrorState message={error} onRetry={run} />}
          {stage && !record && <SkeletonAnswer />}
          {record && <AnswerCard record={record} stage={stage} tab={tab} setTab={setTab} />}
          {!record && !stage && !error && (
            <EmptyAsk suggestions={promptSuggestions} onPick={setPrompt} />
          )}
        </div>

        <div className="ask-side">
          <WhyPanel record={record} />
        </div>
      </div>
    </div>
  );
}
