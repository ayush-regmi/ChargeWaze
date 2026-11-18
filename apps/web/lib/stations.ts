/**
 * Station display helpers and the Charging page's filter state.
 *
 * Filter state lives in the URL query so a reload or a shared link keeps it
 * (phase3_plan.md F6). Station values the provider doesn't give (connector
 * count, power, operator, …) stay missing: nothing here derives or guesses
 * them (PRD §7, D9).
 */

import type { components } from "@chargewise/api-client";

import { haversineKm, roundCoord, type Point } from "@/lib/geo-format";
import { milesToKm, type UnitSystem } from "@/lib/units";

export type Station = components["schemas"]["StationOut"];
export type RouteStation = components["schemas"]["RouteStationOut"];
export type StationConnector = components["schemas"]["StationConnector"];
export type ConnectorType = StationConnector["type"];
export type ChargingLevel = "level2" | "dc_fast";

export const CONNECTOR_TYPES: readonly ConnectorType[] = ["CCS1", "NACS", "CHAdeMO", "J1772"];

const CONNECTOR_LABELS: Record<ConnectorType, string> = {
  CCS1: "CCS1",
  NACS: "NACS (Tesla)",
  CHAdeMO: "CHAdeMO",
  J1772: "J1772",
};

export function connectorLabel(type: ConnectorType): string {
  return CONNECTOR_LABELS[type];
}

export function isConnectorType(value: string): value is ConnectorType {
  return (CONNECTOR_TYPES as readonly string[]).includes(value);
}

/** Vehicle spec connectors are free strings; keep the ones stations use. */
export function vehicleConnectorTypes(connectors: readonly string[] | null | undefined): ConnectorType[] {
  return CONNECTOR_TYPES.filter((t) => connectors?.includes(t));
}

/** "150 kW", or null when the provider gave no power. */
export function formatKw(kw: number | null | undefined): string | null {
  if (kw === null || kw === undefined || !Number.isFinite(kw)) return null;
  return `${kw.toLocaleString(undefined, { maximumFractionDigits: 1 })} kW`;
}

/** Count and power for one connector chip, with "unknown" for missing values. */
export function connectorDetail(c: Pick<StationConnector, "count" | "power_kw">): string {
  const count = c.count ?? null;
  const power = formatKw(c.power_kw);
  const countText = count === null ? "count unknown" : `× ${count}`;
  return `${countText} · ${power ?? "power unknown"}`;
}

export function hasUnknownPower(station: Pick<Station, "max_power_kw">): boolean {
  return station.max_power_kw === null;
}

const STATUS_LABELS: Record<Station["status"], string> = {
  open: "Open",
  planned: "Planned",
  temporarily_unavailable: "Temporarily unavailable",
  unknown: "Unknown",
};

export function statusLabel(status: Station["status"]): string {
  return STATUS_LABELS[status];
}

const ACCESS_LABELS: Record<Station["access"], string> = {
  public: "Public",
  private: "Private",
  unknown: "Unknown",
};

export function accessLabel(access: Station["access"]): string {
  return ACCESS_LABELS[access];
}

/** Street line and "City, Region Postal" line, skipping missing parts. */
export function addressLines(
  s: Pick<Station, "street_address" | "city" | "region" | "postal_code" | "country">,
): string[] {
  const locality = [s.city, [s.region, s.postal_code].filter(Boolean).join(" ")]
    .filter(Boolean)
    .join(", ");
  return [s.street_address, locality, s.country].filter((x): x is string => Boolean(x));
}

/** External navigation links (PRD §4.2): only links, no routing of our own. */
export function directionsLinks(location: { lat: number; lon: number }): {
  google: string;
  apple: string;
} {
  const ll = `${location.lat},${location.lon}`;
  return {
    google: `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(ll)}`,
    apple: `https://maps.apple.com/?daddr=${encodeURIComponent(ll)}`,
  };
}

// ---------------------------------------------------------------------------
// Filter state ↔ URL
// ---------------------------------------------------------------------------

export type RadiusOption = { label: string; km: number };

/** `GET /stations` allows at most 80 km, so "50 mi" is capped there. */
export const MAX_RADIUS_KM = 80;

export function radiusOptions(units: UnitSystem): RadiusOption[] {
  if (units === "imperial") {
    return [5, 10, 25, 50].map((mi) => ({
      label: `${mi} mi`,
      km: Math.min(MAX_RADIUS_KM, roundCoord(milesToKm(mi), 2)),
    }));
  }
  return [10, 25, 50, 80].map((km) => ({ label: `${km} km`, km }));
}

export function defaultRadiusKm(units: UnitSystem): number {
  return radiusOptions(units)[units === "imperial" ? 2 : 1].km;
}

export const MIN_POWER_OPTIONS: readonly number[] = [50, 150, 250];

export type ChargingFilters = {
  location: { lat: number; lon: number; label: string | null } | null;
  /** null = the default (the vehicle's connectors, or any when there are none). */
  connectors: ConnectorType[] | "any" | null;
  networks: string[];
  level: ChargingLevel | null;
  minPowerKw: number | null;
  radiusKm: number | null;
};

export const EMPTY_FILTERS: ChargingFilters = {
  location: null,
  connectors: null,
  networks: [],
  level: null,
  minPowerKw: null,
  radiusKm: null,
};

type ParamsLike = { getAll(name: string): string[]; get(name: string): string | null };

/** Accepts URLSearchParams or a Next.js `searchParams` object. */
export function toParams(
  input: ParamsLike | Record<string, string | string[] | undefined>,
): ParamsLike {
  if (typeof (input as ParamsLike).getAll === "function") return input as ParamsLike;
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(input as Record<string, string | string[] | undefined>)) {
    if (v === undefined) continue;
    for (const item of Array.isArray(v) ? v : [v]) p.append(k, item);
  }
  return p;
}

function finite(value: string | null, min: number, max: number): number | null {
  if (value === null || value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= min && n <= max ? n : null;
}

/** Parse the Charging page query. Invalid values are dropped, never guessed. */
export function parseFilters(
  input: ParamsLike | Record<string, string | string[] | undefined>,
): ChargingFilters {
  const p = toParams(input);
  const lat = finite(p.get("lat"), -90, 90);
  const lon = finite(p.get("lon"), -180, 180);
  const label = p.get("place")?.trim().slice(0, 200) || null;

  const rawConnectors = p.getAll("connector");
  let connectors: ChargingFilters["connectors"] = null;
  if (rawConnectors.includes("any")) {
    connectors = "any";
  } else {
    const valid = CONNECTOR_TYPES.filter((t) => rawConnectors.includes(t));
    if (valid.length > 0) connectors = valid;
  }

  const networks = [
    ...new Set(
      p
        .getAll("network")
        .map((n) => n.trim())
        .filter((n) => n.length > 0 && n.length <= 100),
    ),
  ].slice(0, 20);
  const levelRaw = p.get("level");
  const level = levelRaw === "level2" || levelRaw === "dc_fast" ? levelRaw : null;

  return {
    location: lat !== null && lon !== null ? { lat, lon, label } : null,
    connectors,
    networks,
    level,
    minPowerKw: finite(p.get("min_kw"), 0, 1000),
    radiusKm: finite(p.get("radius"), 0.1, MAX_RADIUS_KM),
  };
}

/** The query string for a filter state (without `?`). Defaults are left out. */
export function serializeFilters(f: ChargingFilters): string {
  const p = new URLSearchParams();
  if (f.location) {
    p.set("lat", String(roundCoord(f.location.lat)));
    p.set("lon", String(roundCoord(f.location.lon)));
    if (f.location.label) p.set("place", f.location.label);
  }
  if (f.connectors === "any") p.append("connector", "any");
  else if (f.connectors) for (const c of f.connectors) p.append("connector", c);
  for (const n of f.networks) p.append("network", n);
  if (f.level) p.set("level", f.level);
  if (f.minPowerKw !== null) p.set("min_kw", String(f.minPowerKw));
  if (f.radiusKm !== null) p.set("radius", String(f.radiusKm));
  return p.toString();
}

/**
 * The connectors a filter state means. An empty list means "any connector".
 * With no explicit choice, the vehicle's connectors are the default (F6).
 */
export function effectiveConnectors(
  f: Pick<ChargingFilters, "connectors">,
  vehicleConnectors: readonly ConnectorType[],
): ConnectorType[] {
  if (f.connectors === "any") return [];
  if (f.connectors) return [...f.connectors];
  return [...vehicleConnectors];
}

/** Toggle one connector, turning the result back into a filter value. */
export function toggleConnector(
  current: readonly ConnectorType[],
  type: ConnectorType,
): ChargingFilters["connectors"] {
  const next = current.includes(type) ? current.filter((c) => c !== type) : [...current, type];
  const ordered = CONNECTOR_TYPES.filter((c) => next.includes(c));
  return ordered.length === 0 ? "any" : ordered;
}

export type StationsQuery = {
  lat: number;
  lon: number;
  radius_km: number;
  connector?: ConnectorType[];
  network?: string[];
  charging_level?: ChargingLevel;
  min_power_kw?: number;
  vehicle_id?: string;
  limit: number;
};

export const STATIONS_PAGE_LIMIT = 100;

/**
 * `GET /api/v1/stations` params for a filter state. The API turns a missing
 * `connector` into the vehicle's connectors, so "any connector" with a
 * vehicle sends every type explicitly.
 */
export function toStationsQuery(
  f: ChargingFilters,
  options: { vehicleId: string | null; vehicleConnectors: readonly ConnectorType[]; units: UnitSystem },
): StationsQuery | null {
  if (!f.location) return null;
  const connectors = effectiveConnectors(f, options.vehicleConnectors);
  const q: StationsQuery = {
    lat: f.location.lat,
    lon: f.location.lon,
    radius_km: f.radiusKm ?? defaultRadiusKm(options.units),
    limit: STATIONS_PAGE_LIMIT,
  };
  if (connectors.length > 0) q.connector = connectors;
  else if (options.vehicleId) q.connector = [...CONNECTOR_TYPES];
  if (f.networks.length > 0) q.network = f.networks;
  if (f.level) q.charging_level = f.level;
  if (f.minPowerKw !== null) q.min_power_kw = f.minPowerKw;
  if (options.vehicleId) q.vehicle_id = options.vehicleId;
  return q;
}

/** Stations with their straight-line distance from `center`, nearest first. */
export function sortByDistance<T extends Pick<Station, "location">>(
  stations: readonly T[],
  center: Point,
): { station: T; distanceKm: number }[] {
  return stations
    .map((station) => ({ station, distanceKm: haversineKm(center, station.location) }))
    .sort((a, b) => a.distanceKm - b.distanceKm);
}

export type StationsErrorCode =
  | "STATIONS_UNREACHABLE"
  | "STATIONS_NOT_CONFIGURED"
  | "VALIDATION_ERROR"
  | "UNKNOWN";

/** Map an API error envelope (api §1.1) to a code the station UI knows. */
export function stationsErrorCode(body: unknown): StationsErrorCode {
  const code =
    body && typeof body === "object" && "error" in body
      ? (body as { error?: { code?: unknown } }).error?.code
      : undefined;
  if (
    code === "STATIONS_UNREACHABLE" ||
    code === "STATIONS_NOT_CONFIGURED" ||
    code === "VALIDATION_ERROR"
  ) {
    return code;
  }
  return "UNKNOWN";
}
