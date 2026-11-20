/**
 * Pure helpers for the planner UI (phase4_plan F6) around
 * `POST /api/v1/routes/plan` (docs/api-contracts.md §3.7): reading its
 * errors, building the per-request `preferences`, labelling stops and
 * cutting the limiting segment out of the route for the map.
 *
 * Nothing here recomputes a plan: every number shown comes from the API.
 */
import type { components } from "@chargewise/api-client";

import type { Preferences, PreferencesBody } from "@/components/profile/preferences";
import { formatSoc } from "@/components/trips/plan-payload";

import { haversineKm, type Point } from "./geo-format";
import { decodePolyline, PolylineDecodeError } from "./polyline";
import { retryAfterSeconds } from "./retry-after";

export type PlanResponse = components["schemas"]["PlanResponse"];
export type PlanStop = components["schemas"]["PlanStopOut"];
export type PlanPreferences = components["schemas"]["PlanPreferences"];

/**
 * The user's settings behind one plan: per-request preferences (only the
 * changed keys) and a starting-charge override. Kept with each route's
 * outcome so "Plan again" and the re-plan form reuse them.
 */
export type PlanRequestSettings = {
  preferences: PlanPreferences | null;
  startingSoc: number | null;
};

export const DEFAULT_PLAN_REQUEST: PlanRequestSettings = { preferences: null, startingSoc: null };

/** Request errors that have their own UI; everything else is a retryable error. */
export type PlanErrorCode =
  | "MAPBOX_NOT_CONFIGURED"
  | "MAPBOX_UNREACHABLE"
  | "MAPBOX_ERROR"
  | "STATIONS_NOT_CONFIGURED"
  | "STATIONS_UNREACHABLE"
  | "NOT_FOUND"
  | "VALIDATION_ERROR"
  | "UNAUTHENTICATED"
  | "UNKNOWN";

/** What one planning request produced. */
export type PlanOutcome =
  | { kind: "plan"; plan: PlanResponse }
  | { kind: "soc_required"; lastSoc: number | null; lastRecordedAt: string | null }
  | { kind: "specs_incomplete"; missing: string[] }
  | { kind: "route_unavailable"; routeCount: number | null }
  | { kind: "preferences_invalid"; fields: string[]; maxChargeSoc: number | null }
  /** The per-user plan limit (§3.7 "Rate limit"); seconds until the next plan, if given. */
  | { kind: "rate_limited"; retryAfterSeconds: number | null }
  | { kind: "error"; code: PlanErrorCode };

const KNOWN_CODES = new Set<PlanErrorCode>([
  "MAPBOX_NOT_CONFIGURED",
  "MAPBOX_UNREACHABLE",
  "MAPBOX_ERROR",
  "STATIONS_NOT_CONFIGURED",
  "STATIONS_UNREACHABLE",
  "NOT_FOUND",
  "VALIDATION_ERROR",
  "UNAUTHENTICATED",
]);

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function fraction(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? value
    : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/**
 * Turn a failed `POST /routes/plan` (status + error envelope) into an outcome.
 * The 422 `details.reason` values each get their own state (§3.7 Errors).
 */
export function planErrorOutcome(body: unknown, status: number): PlanOutcome {
  if (status === 401) return { kind: "error", code: "UNAUTHENTICATED" };
  const error = record(record(body)?.error);
  const code = typeof error?.code === "string" ? error.code : null;
  const details = record(error?.details);

  if (code === "RATE_LIMITED") {
    return { kind: "rate_limited", retryAfterSeconds: retryAfterSeconds(details) };
  }
  if (code === "VALIDATION_ERROR" && details) {
    switch (details.reason) {
      case "STARTING_SOC_REQUIRED":
        return {
          kind: "soc_required",
          lastSoc: fraction(details.last_soc),
          lastRecordedAt: typeof details.last_recorded_at === "string" ? details.last_recorded_at : null,
        };
      case "VEHICLE_SPECS_INCOMPLETE":
        return { kind: "specs_incomplete", missing: strings(details.missing) };
      case "ROUTE_INDEX_UNAVAILABLE":
        return {
          kind: "route_unavailable",
          routeCount: typeof details.route_count === "number" ? details.route_count : null,
        };
    }
    const fields = strings(details.fields);
    if (fields.length > 0) {
      return { kind: "preferences_invalid", fields, maxChargeSoc: fraction(details.max_charge_soc) };
    }
  }
  if (code && KNOWN_CODES.has(code as PlanErrorCode)) {
    return { kind: "error", code: code as PlanErrorCode };
  }
  return { kind: "error", code: "UNKNOWN" };
}

/** Names for `VEHICLE_SPECS_INCOMPLETE` `details.missing` values. */
export const SPEC_FIELD_LABELS: Record<string, string> = {
  usable_capacity_kwh: "usable battery capacity",
  connectors: "charging connectors",
  charging_curve: "charging curve",
  baseline_kwh_per_km: "energy consumption",
};

export function specFieldLabel(field: string): string {
  return SPEC_FIELD_LABELS[field] ?? field;
}

/** "16% → 61%" for a stop; the map label and the list use the same text. */
export function stopSocLabel(stop: {
  arrival_soc: number | null;
  departure_soc: number | null;
}): string {
  return `${formatSoc(stop.arrival_soc) ?? "—"} → ${formatSoc(stop.departure_soc) ?? "—"}`;
}

// ---- Starting SoC override ---------------------------------------------------

/** A typed percentage (0–100, whole number) → fraction, or an error message. */
export function parseSocPercent(raw: string): { value: number } | { error: string } {
  const trimmed = raw.trim();
  if (!/^\d{1,3}$/.test(trimmed)) return { error: "Enter a whole percentage, like 64." };
  const percent = Number(trimmed);
  if (percent > 100) return { error: "Must be between 0 and 100%." };
  return { value: percent / 100 };
}

/** The prompt's starting value: the last reading as a whole percent, else empty. */
export function socPromptDefault(lastSoc: number | null): string {
  return lastSoc === null ? "" : String(Math.round(lastSoc * 100));
}

// ---- Per-request preferences -------------------------------------------------

function sameNetworks(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((n, i) => n === b[i]);
}

/**
 * The `preferences` object for `POST /routes/plan`: only the keys that differ
 * from the stored preferences (the server merges the rest, §3.7). `null` when
 * nothing changed, so the request omits the key.
 */
export function changedPlanPreferences(
  body: PreferencesBody,
  stored: Preferences,
): PlanPreferences | null {
  const out: PlanPreferences = {};
  if (body.reserve_soc !== stored.reserve_soc) out.reserve_soc = body.reserve_soc;
  if (body.preferred_charge_to !== stored.preferred_charge_to) {
    out.preferred_charge_to = body.preferred_charge_to;
  }
  if (body.max_detour_minutes !== stored.max_detour_minutes) {
    out.max_detour_minutes = body.max_detour_minutes;
  }
  if (!sameNetworks(body.preferred_networks, stored.preferred_networks ?? [])) {
    out.preferred_networks = body.preferred_networks;
  }
  return Object.keys(out).length > 0 ? out : null;
}

// ---- Limiting segment ----------------------------------------------------------

/**
 * The part of a route between `fromKm` and `toKm` along it, as points.
 *
 * `fromKm`/`toKm` are on Mapbox's road-distance axis (`route.distance_km`,
 * §3.7), so the decoded line's own length is scaled to that distance before
 * cutting, the same way the API places stations. The ends are interpolated.
 * Returns null when the line can't be decoded or the range is empty.
 */
export function sliceRouteByKm(
  polyline6: string,
  distanceKm: number,
  fromKm: number,
  toKm: number,
): Point[] | null {
  let line: Point[];
  try {
    line = decodePolyline(polyline6).map(([lat, lon]) => ({ lat, lon }));
  } catch (e) {
    if (e instanceof PolylineDecodeError) return null;
    throw e;
  }
  if (line.length < 2 || !(distanceKm > 0) || !(toKm > fromKm)) return null;

  const cumulative = [0];
  for (let i = 1; i < line.length; i++) {
    cumulative.push(cumulative[i - 1] + haversineKm(line[i - 1], line[i]));
  }
  const length = cumulative[cumulative.length - 1];
  if (!(length > 0)) return null;
  const scale = length / distanceKm;
  const from = Math.max(0, Math.min(length, fromKm * scale));
  const to = Math.max(0, Math.min(length, toKm * scale));
  if (!(to > from)) return null;

  const at = (d: number): Point => {
    let i = 1;
    while (i < cumulative.length - 1 && cumulative[i] < d) i++;
    const span = cumulative[i] - cumulative[i - 1];
    const t = span > 0 ? (d - cumulative[i - 1]) / span : 0;
    const a = line[i - 1];
    const b = line[i];
    return { lat: a.lat + (b.lat - a.lat) * t, lon: a.lon + (b.lon - a.lon) * t };
  };

  const out: Point[] = [at(from)];
  for (let i = 0; i < line.length; i++) {
    if (cumulative[i] > from && cumulative[i] < to) out.push(line[i]);
  }
  out.push(at(to));
  return out;
}
