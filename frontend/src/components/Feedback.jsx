import { Icon } from "./Icon";

const STAGES = [
  { key: "routing", label: "Route" },
  { key: "answering", label: "Answer" },
  { key: "verifying", label: "Verify" },
];

export function StageBar({ stage }) {
  const activeIndex = STAGES.findIndex((item) => item.key === stage);
  return (
    <div className="stage-bar" role="status" aria-live="polite">
      {STAGES.map((item, index) => {
        const state =
          activeIndex === -1 ? "idle" : index < activeIndex ? "done" : index === activeIndex ? "active" : "idle";
        return (
          <div className={`stage stage-${state}`} key={item.key}>
            <span className="stage-dot">{state === "done" ? <Icon name="check" size={11} /> : index + 1}</span>
            <span>{item.label}</span>
            {index < STAGES.length - 1 && <span className="stage-line" aria-hidden="true" />}
          </div>
        );
      })}
    </div>
  );
}

export function SkeletonAnswer() {
  return (
    <div className="skeleton-card" aria-hidden="true">
      <div className="skeleton-row">
        <span className="skeleton skeleton-badge" />
        <span className="skeleton skeleton-badge short" />
      </div>
      <span className="skeleton skeleton-line wide" />
      <span className="skeleton skeleton-line" />
      <span className="skeleton skeleton-line medium" />
      <div className="skeleton-divider" />
      <span className="skeleton skeleton-line short" />
      <span className="skeleton skeleton-line medium" />
    </div>
  );
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="error-state" role="alert">
      <div className="error-icon"><Icon name="alert" size={18} /></div>
      <div>
        <h3>That request did not complete</h3>
        <p>{message}</p>
        <p className="muted">
          Check that the gateway is running and reachable, or switch back to mock fixtures.
        </p>
        {onRetry && (
          <button type="button" className="button button-ghost button-small" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyAsk({ suggestions, onPick }) {
  return (
    <div className="empty-ask">
      <div className="empty-orbit" aria-hidden="true">
        <span className="orbit-node node-a" />
        <span className="orbit-node node-b" />
        <span className="orbit-node node-c" />
        <span className="orbit-core"><Icon name="route" size={22} /></span>
      </div>
      <p className="eyebrow">Nothing routed yet</p>
      <h2>Pick a starting point, or write your own.</h2>
      <p className="empty-copy">
        TrustRouter sends simple prompts to Nano, escalates risky ones, then checks every claim
        and repairs the answer on Ultra when a claim fails.
      </p>
      <div className="empty-points">
        <span><Icon name="gauge" size={14} /> Confidence-aware routing</span>
        <span><Icon name="shield" size={14} /> Claim-level verification</span>
        <span><Icon name="spark" size={14} /> One repair pass</span>
      </div>
      <div className="empty-suggestions">
        {suggestions.map((item) => (
          <button
            key={item.label}
            type="button"
            className="suggestion"
            onClick={() => onPick(item.prompt)}
          >
            <span className="suggestion-label">{item.label}</span>
            <span className="suggestion-prompt">{item.prompt}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
