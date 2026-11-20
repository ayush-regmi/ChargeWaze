/**
 * Helpers for Chargewise's own `RATE_LIMITED` (429) error (docs/api-contracts.md
 * §2): the envelope always carries `details.retry_after_seconds`, which the UI
 * turns into "try again in N minutes".
 */

/** `details.retry_after_seconds` from an error envelope's `details`, or `null`. */
export function retryAfterSeconds(details: unknown): number | null {
  if (!details || typeof details !== "object" || Array.isArray(details)) return null;
  const value = (details as Record<string, unknown>).retry_after_seconds;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/** "Try again in 3 minutes." for a wait in seconds; a vaguer hint without one. */
export function tryAgainText(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
    return "Try again in a few minutes.";
  }
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 90) return `Try again in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.`;
  const hours = Math.round(minutes / 60);
  return `Try again in about ${hours} hours.`;
}
