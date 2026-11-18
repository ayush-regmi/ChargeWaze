/**
 * Trip Planner (`/trips`) state ↔ URL query (phase3_plan.md F7).
 *
 * The URL holds everything needed to reload or share a plan: both endpoints
 * (rounded to 4 decimals, about 11 m, plus a label), the selected route, the
 * station filters and the corridor width. It never holds a Search Box session
 * token. Invalid values are dropped, never guessed.
 *
 * Params:
 * - `from_lat`, `from_lon`, `from`: origin and its label.
 * - `to_lat`, `to_lon`, `to`: destination and its label.
 * - `route`: the selected route's `index` (0–2). Missing means 0.
 * - `connector`, `network`, `min_kw`: as on the Charging page (`lib/stations.ts`).
 * - `level`: `level2` or `all`. Missing means DC fast, the along-route default
 *   (D6), so the URL stays short in the usual case.
 * - `corridor`: corridor half-width in km, 0.5–16. Missing means 3.
 */

import { roundCoord } from "@/lib/geo-format";
import {
  CONNECTOR_TYPES,
  EMPTY_FILTERS,
  effectiveConnectors,
  parseFilters,
  serializeFilters,
  toParams,
  type ChargingFilters,
  type ChargingLevel,
  type ConnectorType,
} from "@/lib/stations";

export type TripPoint = { lat: number; lon: number; label: string | null };

/** Station filters for the trip; `location` and `radiusKm` are unused (always null). */
export type TripFilters = ChargingFilters;

export type TripState = {
  origin: TripPoint | null;
  destination: TripPoint | null;
  /** The selected route's `index` from `POST /routes/directions`. */
  routeIndex: number;
  filters: TripFilters;
  /** null = the default corridor (`DEFAULT_CORRIDOR_KM`). */
  corridorKm: number | null;
};

export const DEFAULT_CORRIDOR_KM = 3;
export const MIN_CORRIDOR_KM = 0.5;
export const MAX_CORRIDOR_KM = 16;
/** Directions returns at most three routes (api §3.15). */
export const MAX_ROUTES = 3;
/** The most stations asked for per route (api §3.17 default). */
export const ALONG_ROUTE_LIMIT = 200;

/** DC fast is the default level along a route (D6). */
export const DEFAULT_TRIP_FILTERS: TripFilters = { ...EMPTY_FILTERS, level: "dc_fast" };

export const EMPTY_TRIP: TripState = {
  origin: null,
  destination: null,
  routeIndex: 0,
  filters: DEFAULT_TRIP_FILTERS,
  corridorKm: null,
};

type Input = Parameters<typeof toParams>[0];

function finite(value: string | null, min: number, max: number): number | null {
  if (value === null || value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= min && n <= max ? n : null;
}

function parsePoint(p: ReturnType<typeof toParams>, prefix: "from" | "to"): TripPoint | null {
  const lat = finite(p.get(`${prefix}_lat`), -90, 90);
  const lon = finite(p.get(`${prefix}_lon`), -180, 180);
  if (lat === null || lon === null) return null;
  const label = p.get(prefix)?.trim().slice(0, 200) || null;
  return { lat, lon, label };
}

function parseLevel(raw: string | null): ChargingLevel | null {
  if (raw === "all") return null;
  if (raw === "level2") return "level2";
  return "dc_fast";
}

/** Parse the `/trips` query. Accepts URLSearchParams or a Next.js `searchParams` object. */
export function parseTripState(input: Input): TripState {
  const p = toParams(input);
  const shared = parseFilters(p);
  const route = finite(p.get("route"), 0, MAX_ROUTES - 1);
  return {
    origin: parsePoint(p, "from"),
    destination: parsePoint(p, "to"),
    routeIndex: route !== null && Number.isInteger(route) ? route : 0,
    filters: {
      ...shared,
      location: null,
      radiusKm: null,
      level: parseLevel(p.get("level")),
    },
    corridorKm: finite(p.get("corridor"), MIN_CORRIDOR_KM, MAX_CORRIDOR_KM),
  };
}

/** The query string for a trip state (without `?`). Defaults are left out. */
export function serializeTripState(s: TripState): string {
  const p = new URLSearchParams();
  const point = (prefix: "from" | "to", pt: TripPoint | null) => {
    if (!pt) return;
    p.set(`${prefix}_lat`, String(roundCoord(pt.lat)));
    p.set(`${prefix}_lon`, String(roundCoord(pt.lon)));
    if (pt.label) p.set(prefix, pt.label);
  };
  point("from", s.origin);
  point("to", s.destination);
  if (s.routeIndex > 0) p.set("route", String(s.routeIndex));

  const shared = new URLSearchParams(
    serializeFilters({ ...s.filters, location: null, radiusKm: null, level: null }),
  );
  for (const [k, v] of shared) p.append(k, v);
  if (s.filters.level === null) p.set("level", "all");
  else if (s.filters.level === "level2") p.set("level", "level2");
  if (s.corridorKm !== null) p.set("corridor", String(s.corridorKm));
  return p.toString();
}

export function tripHref(s: TripState): string {
  const qs = serializeTripState(s);
  return qs ? `/trips?${qs}` : "/trips";
}

/** Key for "same origin and destination", so directions are fetched once per pair. */
export function endpointsKey(s: Pick<TripState, "origin" | "destination">): string | null {
  if (!s.origin || !s.destination) return null;
  const c = (pt: TripPoint) => `${roundCoord(pt.lat)},${roundCoord(pt.lon)}`;
  return `${c(s.origin)};${c(s.destination)}`;
}

export function corridorKm(s: Pick<TripState, "corridorKm">): number {
  return s.corridorKm ?? DEFAULT_CORRIDOR_KM;
}

/** `POST /api/v1/stations/along-route` body, without the geometry (api §3.17). */
export type AlongRouteFilters = {
  corridor_km: number;
  connector?: ConnectorType[];
  network?: string[];
  charging_level: ChargingLevel | "all";
  min_power_kw?: number;
  vehicle_id?: string;
  limit: number;
};

/**
 * Along-route filters for a trip state. As on the Charging page, a missing
 * `connector` with a vehicle means "the vehicle's connectors" on the API, so
 * "any connector" with a vehicle sends every type.
 */
export function toAlongRouteFilters(
  s: Pick<TripState, "filters" | "corridorKm">,
  options: { vehicleId: string | null; vehicleConnectors: readonly ConnectorType[] },
): AlongRouteFilters {
  const connectors = effectiveConnectors(s.filters, options.vehicleConnectors);
  const body: AlongRouteFilters = {
    corridor_km: corridorKm(s),
    charging_level: s.filters.level ?? "all",
    limit: ALONG_ROUTE_LIMIT,
  };
  if (connectors.length > 0) body.connector = connectors;
  else if (options.vehicleId) body.connector = [...CONNECTOR_TYPES];
  if (s.filters.networks.length > 0) body.network = s.filters.networks;
  if (s.filters.minPowerKw !== null) body.min_power_kw = s.filters.minPowerKw;
  if (options.vehicleId) body.vehicle_id = options.vehicleId;
  return body;
}

/** Stable key for a set of along-route filters, for caching results per route. */
export function alongRouteFiltersKey(f: AlongRouteFilters): string {
  return JSON.stringify([
    f.corridor_km,
    f.connector ?? null,
    f.network ?? null,
    f.charging_level,
    f.min_power_kw ?? null,
    f.vehicle_id ?? null,
    f.limit,
  ]);
}
