"use client";

import { useState } from "react";

import { serializeFilters, type ChargingFilters } from "@/lib/stations";

type State = {
  /** The URL filters this state was last synced from. */
  propKey: string;
  /** What the controls show: the URL filters plus edits still on their way. */
  local: ChargingFilters;
  /** Query strings of our own navigations that haven't landed yet, oldest first. */
  requested: string[];
};

/**
 * Filter state for a page whose filters live in the URL. The page's
 * `filters` prop only changes once a navigation finishes, so building each
 * edit from the prop would drop an earlier edit that is still in flight.
 * Edits are merged into local state instead, and every navigation is built
 * from that.
 *
 * The local state resyncs to the prop when the URL changes for any other
 * reason (back/forward, a link). When one of our own earlier navigations
 * lands while a later one is still pending, it is skipped, so the controls
 * don't flicker back to an intermediate state.
 */
export function useUrlFilters(
  filters: ChargingFilters,
): [ChargingFilters, (next: ChargingFilters) => void] {
  const propKey = serializeFilters(filters);
  const [state, setState] = useState<State>({ propKey, local: filters, requested: [] });

  let current = state;
  if (state.propKey !== propKey) {
    const landed = state.requested.indexOf(propKey);
    current =
      landed >= 0 && landed < state.requested.length - 1
        ? { ...state, propKey, requested: state.requested.slice(landed + 1) }
        : { propKey, local: filters, requested: [] };
    // Adjusting state while rendering (react.dev "storing information from
    // previous renders"): React re-renders immediately with the new state.
    setState(current);
  }

  const update = (next: ChargingFilters) => {
    setState((s) => ({ ...s, local: next, requested: [...s.requested, serializeFilters(next)] }));
  };

  return [current.local, update];
}
