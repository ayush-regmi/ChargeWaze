import {
  QUALITY_LABELS,
  qualityReasonText,
  REVIEW_LABELS,
  type QualityFlag,
  type ReviewStatus,
} from "./observation-format";

const QUALITY_CLASSES: Record<QualityFlag, string> = {
  high: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  medium: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300",
  low: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
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
      className="list-disc space-y-0.5 pl-5 text-xs text-neutral-600 dark:text-neutral-400"
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
  auto: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  needs_review: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300",
  confirmed: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  rejected: "bg-neutral-200 text-neutral-700 line-through dark:bg-neutral-800 dark:text-neutral-400",
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
