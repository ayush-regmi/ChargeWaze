import type { TripDetailData } from "@/components/trips/trip-detail";
import type { TripListItem } from "@/components/trips/trip-list";

export const TRIP_ID = "6f1c2d3e-4a5b-4c6d-8e7f-9a0b1c2d3e4f";
export const VEHICLE_ID = "0b6c1f7e-2d3a-4c5b-8e9f-0a1b2c3d4e5f";
export const STATION_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";

export function makeListItem(overrides: Partial<TripListItem> = {}): TripListItem {
  return {
    id: TRIP_ID,
    vehicle_id: VEHICLE_ID,
    origin_label: "Frederick, MD",
    destination_label: "Toronto, ON",
    status: "planned",
    plan_status: "feasible",
    distance_km: 748.1,
    total_minutes: 583,
    stop_count: 3,
    created_at: "2026-11-06T18:00:00Z",
    saved_at: "2026-11-06T18:02:00Z",
    ...overrides,
  };
}

/** The feasible §3.7 example body (one stop shown). */
export function feasiblePlan(): Record<string, unknown> {
  return {
    trip_id: TRIP_ID,
    created_at: "2026-11-06T18:00:00Z",
    status: "feasible",
    model_version: "deterministic-v1",
    vehicle_id: VEHICLE_ID,
    origin: { lat: 39.4143, lon: -77.4105, label: "Frederick, MD" },
    destination: { lat: 43.6532, lon: -79.3832, label: "Toronto, ON" },
    departure_time: null,
    starting_soc: 0.9,
    starting_soc_source: "telemetry",
    starting_soc_recorded_at: "2026-11-06T17:55:00Z",
    route: {
      route_index: 0,
      geometry_polyline6: "_c`|@_c`|@",
      distance_km: 748.1,
      drive_minutes: 515,
      summary: "US 15, QEW",
      countries: ["US", "CA"],
    },
    stops: [
      {
        sequence: 1,
        station: {
          id: STATION_ID,
          name: "Example station",
          network: "Electrify America",
          connector: "CCS1",
          power_kw: 150,
          location: { lat: 40.2732, lon: -76.8867 },
        },
        distance_along_route_km: 260.0,
        distance_from_route_km: 0.8,
        arrival_soc: 0.162,
        departure_soc: 0.61,
        charging_minutes: 15,
        detour_minutes: 2,
        detour_is_estimate: true,
      },
    ],
    arrival_soc: 0.15,
    totals: {
      drive_minutes: 515,
      charging_minutes: 45,
      detour_minutes: 8,
      stop_overhead_minutes: 15,
      waiting_minutes: 0,
      total_minutes: 583,
      energy_kwh_estimated: 150.92,
    },
    assumptions: {
      usable_capacity_kwh: 71.0,
      usable_capacity_source: "catalog",
      baseline_kwh_per_km: 0.2,
      baseline_source: "catalog",
      charging_curve_source: "catalog_derived",
      adjustments_applied: [],
      reserve_soc: 0.15,
      charge_to_soc: 0.8,
      max_detour_minutes: 20,
      preferred_networks: ["Tesla Supercharger", "EVgo"],
      preferred_networks_relaxed: false,
      stop_overhead_minutes: 5,
      detour_road_factor: 1.3,
      detour_speed_kmh: 50,
      soc_grid_step: 0.01,
    },
    candidates: {
      searched: true,
      corridor_km: 6.4,
      considered: 41,
      excluded_incompatible: 12,
      excluded_unknown_power: 3,
      excluded_detour: 5,
    },
    alternatives: [],
    reason: null,
  };
}

/** The infeasible §3.7 example body. */
export function infeasiblePlan(): Record<string, unknown> {
  return {
    ...feasiblePlan(),
    status: "infeasible",
    stops: [],
    arrival_soc: null,
    totals: null,
    reason: {
      code: "DESTINATION_UNREACHABLE",
      message: "No charger is close enough to cross the gap after km 412 with your charge-to limit.",
      limiting_segment: {
        from: { lat: 42.11, lon: -78.21 },
        to: { lat: 42.98, lon: -78.87 },
        from_km: 412.0,
        to_km: 640.0,
        required_soc: 0.79,
        available_soc: 0.8,
      },
    },
  };
}

export function makeDetail(
  plan: Record<string, unknown>,
  overrides: Partial<TripDetailData> = {},
): TripDetailData {
  return {
    id: TRIP_ID,
    vehicle_id: VEHICLE_ID,
    status: "planned",
    plan_status: plan.status === "infeasible" ? "infeasible" : "feasible",
    created_at: "2026-11-06T18:00:00Z",
    saved_at: "2026-11-06T18:02:00Z",
    plan,
    observation: null,
    ...overrides,
  };
}
