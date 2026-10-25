import { StatusBadge, VerdictBadge } from "./Badges";
import { Icon } from "./Icon";

function sourceHost(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function emptyCopy(status) {
  if (status === "provisional") return "Verification is still running. Claims will land here.";
  if (status === "unchecked") return "No verifier was available, so this answer was not checked.";
  if (status === "failed") return "No answer was produced.";
  return "The verifier found no factual claims to check.";
}

export function ClaimList({ claims = [], status = "verified", compact = false }) {
  if (!claims.length) {
    return (
      <div className={`claims-empty claims-empty-${status}`}>
        <Icon name={status === "unchecked" ? "alert" : "search"} size={15} />
        <span>{emptyCopy(status)}</span>
      </div>
    );
  }
  return (
    <ul className={`claim-list ${compact ? "claim-list-compact" : ""}`}>
      {claims.map((item, index) => {
        const verdict = item.verdict || "unclear";
        const icon = verdict === "supported" ? "check" : verdict === "unsupported" ? "x" : "help";
        return (
          <li className={`claim claim-${verdict}`} key={`${item.text}-${index}`}>
            <span className="claim-icon" aria-hidden="true">
              <Icon name={icon} size={14} />
            </span>
            <div className="claim-body">
              <p className="claim-text">{item.text}</p>
              {item.evidence_snippet && !compact && (
                <p className="claim-evidence">“{item.evidence_snippet}”</p>
              )}
              <div className="claim-meta">
                <VerdictBadge verdict={verdict} compact={compact} />
                {item.source_url ? (
                  <a
                    className="source-link"
                    href={item.source_url}
                    target="_blank"
                    rel="noreferrer"
                    title={item.source_url}
                  >
                    <Icon name="link" size={12} />
                    <span>{sourceHost(item.source_url)}</span>
                  </a>
                ) : (
                  <span className="source-none">No source matched</span>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function ClaimSummary({ claims = [], status }) {
  if (!claims.length && status === "unchecked") {
    return (
      <div className="claim-summary-summary">
        <StatusBadge status={status} size="sm" />
        <span>Unverified by design</span>
      </div>
    );
  }
  return null;
}
