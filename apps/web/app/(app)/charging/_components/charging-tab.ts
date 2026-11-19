import type { components } from "@chargewise/api-client";

import { parseFilters, serializeFilters } from "@/lib/stations";

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

/**
 * Links for a station page opened from `/charging`: `back` returns to the
 * same search and tab, `self` is the sign-in return path. The filters are
 * re-parsed and only `view=favorites` is kept, so nothing is echoed raw.
 */
export function stationPageLinks(
  stationId: string,
  params: Record<string, string | string[] | undefined>,
): { back: string; self: string } {
  const qs = serializeFilters(parseFilters(params));
  const view = parseChargingView(params);
  return {
    back: withView(qs ? `/charging?${qs}` : "/charging", view),
    self: withView(`/charging/${stationId}${qs ? `?${qs}` : ""}`, view),
  };
}
