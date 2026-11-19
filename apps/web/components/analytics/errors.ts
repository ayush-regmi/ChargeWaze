/**
 * Reading the API's error envelope (api-contracts §1.1) for the analytics
 * routes: `{ "error": { "code", "message", "details": { "reason", … } } }`.
 */

export function errorReason(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const error = (body as { error?: unknown }).error;
  if (!error || typeof error !== "object") return null;
  const details = (error as { details?: unknown }).details;
  if (!details || typeof details !== "object") return null;
  const reason = (details as { reason?: unknown }).reason;
  return typeof reason === "string" ? reason : null;
}

/** What to tell the user about a 422 from `/analytics/consumption` (contract §3.11). */
export function rangeErrorMessage(reason: string | null): string {
  switch (reason) {
    case "RANGE_TOO_LARGE":
      return "That range has too many periods to chart (366 at most). Pick a shorter range or group by week or month.";
    case "INVALID_RANGE":
      return "The start date is after the end date.";
    case "UNKNOWN_TIME_ZONE":
      return "Your time zone isn't recognised.";
    default:
      return "Those filters aren't valid.";
  }
}
