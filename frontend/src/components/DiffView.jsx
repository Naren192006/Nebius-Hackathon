import { TierBadge } from "./Badges";
import { Icon } from "./Icon";

export function DiffView({ record }) {
  if (!record?.repair) return null;
  return (
    <div className="diff" role="group" aria-label="Answer before and after repair">
      <div className="diff-column diff-before">
        <div className="diff-label">
          <span className="eyebrow">Before repair</span>
          <span className="diff-tag">Draft</span>
        </div>
        <p>{record.repair.previous_text}</p>
      </div>
      <div className="diff-arrow" aria-hidden="true">
        <Icon name="arrowRight" size={18} />
      </div>
      <div className="diff-column diff-after">
        <div className="diff-label">
          <span className="eyebrow">Delivered answer</span>
          <span className="diff-tag diff-tag-strong">
            <TierBadge tier="ultra" size="sm" /> repair
          </span>
        </div>
        <p>{record.answer.text}</p>
      </div>
    </div>
  );
}
