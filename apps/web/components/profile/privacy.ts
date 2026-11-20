import type { components } from "@chargewise/api-client";

export type PrivacySettings = components["schemas"]["PrivacyResponse"];
export type ConsentStatus = components["schemas"]["ConsentStatus"];

/** What the user types to confirm account deletion. */
export const DELETE_CONFIRMATION = "DELETE";

/** Whole years when the window is a multiple of 365 days, else days. */
export function formatRetention(days: number): string {
  if (days >= 365 && days % 365 === 0) {
    const years = days / 365;
    return years === 1 ? "1 year" : `${years} years`;
  }
  return days === 1 ? "1 day" : `${days} days`;
}
