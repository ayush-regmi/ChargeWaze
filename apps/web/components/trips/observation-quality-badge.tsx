import {
  QUALITY_LABELS,
  qualityReasonText,
  REVIEW_LABELS,
  type QualityFlag,
  type ReviewStatus,
} from "./observation-format";

const QUALITY_CLASSES: Record<QualityFlag, string> = {
  high: "bg-success/10 text-success",
  medium: "bg-warning/10 text-warning",
  low: "bg-destructive/10 text-destructive",
};

/** High / Medium / Low measurement quality (phase5_plan D8). */
export function QualityBadge({ flag }: { flag: QualityFlag }) {
  return (
    <span
      data-testid="quality-badge"
      data-quality={flag}
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${QUALITY_CLASSES[flag]}`}
    >
      {QUALITY_LABELS[flag]}
    </span>
  );
}

/**
 * Every rule the drive missed, as visible text (not hover-only, so it reads
 * the same on touch screens and to screen readers).
 */
export function QualityReasons({ reasons }: { reasons: readonly string[] }) {
  if (reasons.length === 0) return null;
  return (
    <ul
      aria-label="Why this quality"
      data-testid="quality-reasons"
      className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground"
    >
      {reasons.map((code) => (
        <li key={code} data-reason={code}>
          {qualityReasonText(code)}
        </li>
      ))}
    </ul>
  );
}

const REVIEW_CLASSES: Record<ReviewStatus, string> = {
  auto: "bg-muted text-foreground",
  needs_review: "bg-warning/10 text-warning",
  confirmed: "bg-muted text-foreground",
  rejected: "bg-muted text-foreground line-through",
};

export function ReviewStatusBadge({ status }: { status: ReviewStatus }) {
  return (
    <span
      data-testid="review-status"
      data-status={status}
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${REVIEW_CLASSES[status]}`}
    >
      {REVIEW_LABELS[status]}
    </span>
  );
}
