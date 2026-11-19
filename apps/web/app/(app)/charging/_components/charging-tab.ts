import type { components } from "@chargewise/api-client";

/**
 * The `/charging` tabs (phase3_plan.md F8). A plain module, not a client one,
 * so the server page can parse the tab too.
 */

export type FavoriteStation = components["schemas"]["FavoriteStationOut"];

/** Which tab of `/charging` is open: a search near a place, or the user's favorites. */
export type ChargingTab = "nearby" | "favorites";

export function parseChargingView(
  params: Record<string, string | string[] | undefined>,
): ChargingTab {
  return params.view === "favorites" ? "favorites" : "nearby";
}

/** Add the tab to a `/charging` href; Nearby is the default and isn't written. */
export function withView(href: string, view: ChargingTab): string {
  if (view === "nearby") return href;
  return `${href}${href.includes("?") ? "&" : "?"}view=favorites`;
}
