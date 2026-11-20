"use client";

import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";

import { confirmObservedTrip, rejectObservedTrip } from "./observation-actions";
import { REVIEW_LABELS, type ReviewStatus } from "./observation-format";

/**
 * Confirm / Reject for one observed drive (§3.18c–d). Both are reversible
 * (a rejected drive can be confirmed later and the other way round), so
 * there is no extra confirm step. The list refreshes through the action's
 * `revalidatePath`; until then the new status is shown here.
 */
export function ObservationReviewButtons({
  id,
  status: initial,
  label,
}: {
  id: string;
  status: ReviewStatus;
  /** Names the drive for screen readers, e.g. "drive on Nov 10, 28.6 km". */
  label: string;
}) {
  const [status, setStatus] = useState<ReviewStatus>(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<"confirm" | "reject" | null>(null);
  const [, startTransition] = useTransition();

  function run(action: "confirm" | "reject") {
    setError(null);
    setPending(action);
    startTransition(async () => {
      const fn = action === "confirm" ? confirmObservedTrip : rejectObservedTrip;
      const result = await fn(id);
      if ("error" in result) setError(result.error);
      else setStatus(result.status);
      setPending(null);
    });
  }

  const busy = pending !== null;
  return (
    <div className="space-y-1" data-testid="review-buttons" data-status={status}>
      <div className="flex flex-wrap items-center gap-2">
        {status !== "confirmed" ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => run("confirm")}
            disabled={busy}
            aria-label={`Confirm ${label}`}
          >
            {pending === "confirm" ? "Confirming…" : "Confirm"}
          </Button>
        ) : null}
        {status !== "rejected" ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => run("reject")}
            disabled={busy}
            aria-label={`Reject ${label}`}
          >
            {pending === "reject" ? "Rejecting…" : "Reject"}
          </Button>
        ) : null}
        <span aria-live="polite" className="text-xs text-muted-foreground">
          {status !== initial ? `${REVIEW_LABELS[status]}.` : ""}
        </span>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
