/**
 * Observed drives and plan observations (phase5_plan F7, api-contracts
 * §3.18 and §3.18e): parsing, time windows, quality reasons and the
 * planned-vs-observed leg pairing. Pure functions, no React.
 *
 * `observation` on `GET /trips/{id}` is the typed `TripObservation` from the
 * generated client (P5-F4), so the API's validation is the shape check here.
 */

import type { components } from "@chargewaze/api-client";

import type { StoredPlan } from "./plan-payload";

export type ObservedTrip = components["schemas"]["ObservedTripOut"];
export type QualityFlag = ObservedTrip["quality_flag"];
export type ReviewStatus = ObservedTrip["review_status"];

export type TripObservation = components["schemas"]["TripObservation"];
export type ObservationLeg = components["schemas"]["TripObservationLeg"];

/**
 * Read `trip.observation` (§3.18e). Null when the plan isn't matched yet or
 * the observation has no legs; otherwise a copy with the legs in plan order
 * (`plan_leg_index`), which the leg pairing relies on.
 */
export function parseObservation(
  observation: TripObservation | null | undefined,
): TripObservation | null {
  if (!observation || observation.legs.length === 0) return null;
  const legs = [...observation.legs].sort((a, b) => a.plan_leg_index - b.plan_leg_index);
  return { ...observation, legs };
}

// ---- Times ---------------------------------------------------------------

/** `timeZone` undefined = the runtime's own zone (the viewer's, in the browser). */
function clock(d: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).format(d);
}

/** YYYY-MM-DD of `d` in `timeZone` (the runtime's zone when undefined). */
export function dayKey(d: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone,
  }).format(d);
}

/** "Nov 9" (with the year when it isn't `now`'s year). */
export function formatDay(iso: string, timeZone?: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const sameYear = dayKey(d, timeZone).slice(0, 4) === dayKey(now, timeZone).slice(0, 4);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
    timeZone,
  }).format(d);
}

/** "08:41" in 24-hour time. */
export function formatClock(iso: string, timeZone?: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : clock(d, timeZone);
}

/**
 * When the drive started. An exact start is one time ("18:12"); a bounded
 * start (§3.18 Times) is a range, "between 18:12 and 08:41", never a single
 * made-up time. When the two ends fall on different days each end carries
 * its date: "between Nov 9, 18:12 and Nov 10, 08:41".
 */
export function formatStartWindow(
  leg: Pick<ObservedTrip, "start_time" | "start_time_latest" | "start_time_is_exact">,
  timeZone?: string,
  now: Date = new Date(),
): string {
  const a = new Date(leg.start_time);
  const b = new Date(leg.start_time_latest);
  if (Number.isNaN(a.getTime())) return leg.start_time;
  // Two different bounds are always shown as a range, whatever the flag says.
  if (Number.isNaN(b.getTime()) || a.getTime() === b.getTime()) return clock(a, timeZone);
  if (dayKey(a, timeZone) === dayKey(b, timeZone)) {
    return `between ${clock(a, timeZone)} and ${clock(b, timeZone)}`;
  }
  return (
    `between ${formatDay(leg.start_time, timeZone, now)}, ${clock(a, timeZone)}` +
    ` and ${formatDay(leg.start_time_latest, timeZone, now)}, ${clock(b, timeZone)}`
  );
}

// ---- Quality and review --------------------------------------------------

export const QUALITY_LABELS: Record<QualityFlag, string> = {
  high: "High quality",
  medium: "Medium quality",
  low: "Low quality",
};

/** Plain-language text for each D8 reason code (phase5_plan §2.3). */
export const QUALITY_REASON_TEXT: Record<string, string> = {
  soc_missing: "No battery reading at the start or end of the drive",
  soc_increased: "The battery charge rose during the drive, so energy can't be measured",
  capacity_unknown: "The battery's usable capacity is unknown, so energy can't be calculated",
  soc_delta_small: "Too little charge was used to measure energy precisely",
  soc_reading_stale: "A battery reading was taken well before the start or after the end",
  start_time_bounded: "The exact start time isn't known",
  odometer_regression: "An odometer reading went backwards and was ignored",
};

export function qualityReasonText(code: string): string {
  return QUALITY_REASON_TEXT[code] ?? code;
}

export const REVIEW_LABELS: Record<ReviewStatus, string> = {
  auto: "Counted automatically",
  needs_review: "Needs review",
  confirmed: "Confirmed",
  rejected: "Rejected",
};

// ---- Numbers ------------------------------------------------------------

export function formatKwh(kwh: number | null | undefined, digits = 1): string | null {
  if (kwh === null || kwh === undefined || !Number.isFinite(kwh)) return null;
  return `${kwh.toFixed(digits)} kWh`;
}

/** "+0.7 pts" / "−0.3 pts" / "0 pts". */
export function formatPoints(points: number | null | undefined): string | null {
  if (points === null || points === undefined || !Number.isFinite(points)) return null;
  const r = Math.round(points * 10) / 10;
  if (r === 0) return "0 pts";
  return `${r > 0 ? "+" : "−"}${Math.abs(r).toFixed(1)} pts`;
}

/** "+0.29 kWh" / "−0.29 kWh". */
export function formatSignedKwh(kwh: number | null | undefined): string | null {
  if (kwh === null || kwh === undefined || !Number.isFinite(kwh)) return null;
  const r = Math.round(kwh * 100) / 100;
  if (r === 0) return "0.00 kWh";
  return `${r > 0 ? "+" : "−"}${Math.abs(r).toFixed(2)} kWh`;
}

/** Whole-percent SoC from the API ("61%"). */
export function formatPercent(p: number | null | undefined): string | null {
  if (p === null || p === undefined || !Number.isFinite(p)) return null;
  return `${Math.round(p)}%`;
}

// ---- Planned vs observed legs -------------------------------------------

export type PlannedLeg = {
  index: number;
  /** Distance along the route between the stops; detours aren't included. */
  distance_km: number | null;
  start_soc: number | null;
  arrival_soc: number | null;
};

export type LegPair = {
  planned: PlannedLeg;
  observed: ObservationLeg;
  /** Observed − planned arrival charge, in percentage points. */
  arrival_soc_error_points: number | null;
};

/**
 * Pair planned leg n (origin or stop n − 1 → stop n or destination) with
 * observed leg n. Only when the counts line up (§3.18e); otherwise null and
 * the panel shows the observed legs on their own.
 */
export function pairLegs(plan: StoredPlan | null, observation: TripObservation): LegPair[] | null {
  if (!plan) return null;
  const stops = [...plan.stops].sort((a, b) => a.sequence - b.sequence);
  if (stops.length + 1 !== observation.legs.length) return null;

  const along = [0, ...stops.map((s) => s.distance_along_route_km), plan.route?.distance_km ?? null];
  return observation.legs.map((observed, i) => {
    const from = along[i];
    const to = along[i + 1];
    const startSoc = i === 0 ? plan.starting_soc : (stops[i - 1]?.departure_soc ?? null);
    const arrivalSoc = i < stops.length ? (stops[i]?.arrival_soc ?? null) : plan.arrival_soc;
    const distance =
      from !== null && from !== undefined && to !== null && to !== undefined && to >= from
        ? to - from
        : null;
    const err =
      arrivalSoc !== null && observed.soc_end_percent !== null
        ? observed.soc_end_percent - arrivalSoc * 100
        : null;
    return {
      planned: { index: i + 1, distance_km: distance, start_soc: startSoc, arrival_soc: arrivalSoc },
      observed,
      arrival_soc_error_points: err,
    };
  });
}
