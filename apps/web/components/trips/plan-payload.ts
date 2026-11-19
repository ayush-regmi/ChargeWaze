/**
 * The stored `POST /routes/plan` body (docs/api-contracts.md §3.7), as
 * returned unchanged in `GET /trips/{id}` → `plan` (§3.9).
 *
 * The API types `plan` as a free-form object, so this module reads it
 * defensively: a missing or wrong-typed field becomes `null` (shown as
 * missing), never a guessed value and never a crash. Nothing is recomputed.
 */

export type PlanPoint = { lat: number; lon: number; label: string | null };

export type PlanRoute = {
  route_index: number;
  geometry_polyline6: string | null;
  distance_km: number | null;
  drive_minutes: number | null;
  summary: string | null;
};

export type PlanStop = {
  sequence: number;
  station: {
    id: string | null;
    name: string | null;
    network: string | null;
    connector: string | null;
    power_kw: number | null;
    location: { lat: number; lon: number } | null;
  };
  distance_along_route_km: number | null;
  distance_from_route_km: number | null;
  arrival_soc: number | null;
  departure_soc: number | null;
  charging_minutes: number | null;
  detour_minutes: number | null;
};

export type PlanTotals = {
  drive_minutes: number | null;
  charging_minutes: number | null;
  detour_minutes: number | null;
  stop_overhead_minutes: number | null;
  waiting_minutes: number | null;
  total_minutes: number | null;
  energy_kwh_estimated: number | null;
};

export type PlanReason = {
  code: string | null;
  message: string | null;
  limiting_segment: {
    from_km: number | null;
    to_km: number | null;
    required_soc: number | null;
    available_soc: number | null;
  } | null;
};

export type PlanAssumptions = {
  usable_capacity_kwh: number | null;
  usable_capacity_source: string | null;
  baseline_kwh_per_km: number | null;
  baseline_source: string | null;
  charging_curve_source: string | null;
  reserve_soc: number | null;
  charge_to_soc: number | null;
  max_detour_minutes: number | null;
  preferred_networks: string[];
  preferred_networks_relaxed: boolean | null;
  stop_overhead_minutes: number | null;
};

export type PlanCandidates = {
  searched: boolean | null;
  considered: number | null;
  excluded_incompatible: number | null;
  excluded_unknown_power: number | null;
  excluded_detour: number | null;
};

export type StoredPlan = {
  status: "feasible" | "infeasible" | null;
  origin: PlanPoint | null;
  destination: PlanPoint | null;
  departure_time: string | null;
  starting_soc: number | null;
  starting_soc_source: string | null;
  route: PlanRoute | null;
  stops: PlanStop[];
  arrival_soc: number | null;
  totals: PlanTotals | null;
  assumptions: PlanAssumptions | null;
  candidates: PlanCandidates | null;
  reason: PlanReason | null;
};

type Obj = Record<string, unknown>;

function obj(v: unknown): Obj | null {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Obj) : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function latLon(v: unknown): { lat: number; lon: number } | null {
  const o = obj(v);
  if (!o) return null;
  const lat = num(o.lat);
  const lon = num(o.lon);
  if (lat === null || lon === null || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon };
}

function point(v: unknown): PlanPoint | null {
  const p = latLon(v);
  return p ? { ...p, label: str(obj(v)?.label) } : null;
}

function route(v: unknown): PlanRoute | null {
  const o = obj(v);
  if (!o) return null;
  return {
    route_index: num(o.route_index) ?? 0,
    geometry_polyline6: str(o.geometry_polyline6),
    distance_km: num(o.distance_km),
    drive_minutes: num(o.drive_minutes),
    summary: str(o.summary),
  };
}

function stop(v: unknown, i: number): PlanStop | null {
  const o = obj(v);
  if (!o) return null;
  const s = obj(o.station) ?? {};
  return {
    sequence: num(o.sequence) ?? i + 1,
    station: {
      id: str(s.id),
      name: str(s.name),
      network: str(s.network),
      connector: str(s.connector),
      power_kw: num(s.power_kw),
      location: latLon(s.location),
    },
    distance_along_route_km: num(o.distance_along_route_km),
    distance_from_route_km: num(o.distance_from_route_km),
    arrival_soc: num(o.arrival_soc),
    departure_soc: num(o.departure_soc),
    charging_minutes: num(o.charging_minutes),
    detour_minutes: num(o.detour_minutes),
  };
}

function totals(v: unknown): PlanTotals | null {
  const o = obj(v);
  if (!o) return null;
  return {
    drive_minutes: num(o.drive_minutes),
    charging_minutes: num(o.charging_minutes),
    detour_minutes: num(o.detour_minutes),
    stop_overhead_minutes: num(o.stop_overhead_minutes),
    waiting_minutes: num(o.waiting_minutes),
    total_minutes: num(o.total_minutes),
    energy_kwh_estimated: num(o.energy_kwh_estimated),
  };
}

function reason(v: unknown): PlanReason | null {
  const o = obj(v);
  if (!o) return null;
  const seg = obj(o.limiting_segment);
  return {
    code: str(o.code),
    message: str(o.message),
    limiting_segment: seg
      ? {
          from_km: num(seg.from_km),
          to_km: num(seg.to_km),
          required_soc: num(seg.required_soc),
          available_soc: num(seg.available_soc),
        }
      : null,
  };
}

function assumptions(v: unknown): PlanAssumptions | null {
  const o = obj(v);
  if (!o) return null;
  return {
    usable_capacity_kwh: num(o.usable_capacity_kwh),
    usable_capacity_source: str(o.usable_capacity_source),
    baseline_kwh_per_km: num(o.baseline_kwh_per_km),
    baseline_source: str(o.baseline_source),
    charging_curve_source: str(o.charging_curve_source),
    reserve_soc: num(o.reserve_soc),
    charge_to_soc: num(o.charge_to_soc),
    max_detour_minutes: num(o.max_detour_minutes),
    preferred_networks: Array.isArray(o.preferred_networks)
      ? o.preferred_networks.filter((n): n is string => typeof n === "string")
      : [],
    preferred_networks_relaxed: bool(o.preferred_networks_relaxed),
    stop_overhead_minutes: num(o.stop_overhead_minutes),
  };
}

function candidates(v: unknown): PlanCandidates | null {
  const o = obj(v);
  if (!o) return null;
  return {
    searched: bool(o.searched),
    considered: num(o.considered),
    excluded_incompatible: num(o.excluded_incompatible),
    excluded_unknown_power: num(o.excluded_unknown_power),
    excluded_detour: num(o.excluded_detour),
  };
}

/** Read a stored plan body. Returns null only when `raw` isn't an object at all. */
export function parseStoredPlan(raw: unknown): StoredPlan | null {
  const o = obj(raw);
  if (!o) return null;
  const status = o.status === "feasible" || o.status === "infeasible" ? o.status : null;
  const stops = Array.isArray(o.stops)
    ? o.stops.map(stop).filter((s): s is PlanStop => s !== null)
    : [];
  return {
    status,
    origin: point(o.origin),
    destination: point(o.destination),
    departure_time: str(o.departure_time),
    starting_soc: num(o.starting_soc),
    starting_soc_source: str(o.starting_soc_source),
    route: route(o.route),
    stops: [...stops].sort((a, b) => a.sequence - b.sequence),
    arrival_soc: num(o.arrival_soc),
    totals: totals(o.totals),
    assumptions: assumptions(o.assumptions),
    candidates: candidates(o.candidates),
    reason: reason(o.reason),
  };
}

/** A SoC fraction (0..1) as a whole percent, e.g. 0.162 → "16%". Null → null. */
export function formatSoc(fraction: number | null | undefined): string | null {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return null;
  return `${Math.round(fraction * 100)}%`;
}

/** Whole minutes as "45 min" or "9 h 43 min". Null → null. */
export function formatMinutes(minutes: number | null | undefined): string | null {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return null;
  const m = Math.max(0, Math.round(minutes));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest === 0 ? `${h} h` : `${h} h ${rest} min`;
}

/** Human labels for the §3.7 infeasibility codes. Unknown codes show as-is. */
export const INFEASIBLE_TITLES: Record<string, string> = {
  NO_DRIVING_ROUTE: "No driving route",
  STARTING_SOC_TOO_LOW: "Starting charge too low",
  RESERVE_SOC_UNREACHABLE: "Reserve can't be kept",
  NO_COMPATIBLE_CHARGER_IN_RANGE: "No compatible charger in range",
  DESTINATION_UNREACHABLE: "Destination unreachable",
};
