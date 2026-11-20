import type { ObservedTrip, TripObservation } from "@/components/trips/observation-format";

import { feasiblePlan, TRIP_ID, VEHICLE_ID } from "./trip-fixture";

export const LEG_1 = "a1111111-1111-4111-8111-111111111111";
export const LEG_2 = "a2222222-2222-4222-8222-222222222222";

/** phase5_plan §5 example 3: one DC stop, planned 0.8 → 0.173 / 0.66 → 0.153. */
export function examplePlan(): Record<string, unknown> {
  const base = feasiblePlan();
  const [stop] = base.stops as Record<string, unknown>[];
  return {
    ...base,
    starting_soc: 0.8,
    route: { ...(base.route as object), distance_km: 400.0 },
    stops: [{ ...stop, distance_along_route_km: 220.0, arrival_soc: 0.173, departure_soc: 0.66 }],
    arrival_soc: 0.15296,
  };
}

/** The §3.18e example body. */
export function exampleObservation(): TripObservation {
  return {
    matched_at: "2026-11-08T13:41:00Z",
    legs: [
      {
        id: LEG_1,
        plan_leg_index: 1,
        start_time: "2026-11-08T08:02:00Z",
        end_time: "2026-11-08T10:32:00Z",
        distance_km: 222.4,
        soc_start_percent: 80,
        soc_end_percent: 18,
        energy_kwh: 44.02,
        quality_flag: "high",
        review_status: "auto",
      },
      {
        id: LEG_2,
        plan_leg_index: 2,
        start_time: "2026-11-08T10:53:00Z",
        end_time: "2026-11-08T13:00:00Z",
        distance_km: 183.6,
        soc_start_percent: 66,
        soc_end_percent: 15,
        energy_kwh: 36.21,
        quality_flag: "high",
        review_status: "auto",
      },
    ],
    observed_distance_km: 406.0,
    planned_distance_km: 402.6,
    predicted_energy_kwh: 80.52,
    observed_energy_kwh: 80.23,
    error_kwh: -0.29,
    observed_start_soc: 0.8,
    predicted_arrival_soc: 0.153,
    observed_arrival_soc: 0.15,
    arrival_soc_error_points: -0.3,
    model_version: "deterministic-v1",
  };
}

/** The §3.18 list example: a key-off-only, low-quality, bounded leg. */
export function makeObservedTrip(overrides: Partial<ObservedTrip> = {}): ObservedTrip {
  return {
    id: LEG_1,
    vehicle_id: VEHICLE_ID,
    start_time: "2026-11-09T18:12:00Z",
    start_time_latest: "2026-11-10T08:41:00Z",
    end_time: "2026-11-10T08:41:00Z",
    start_time_is_exact: false,
    distance_km: 28.6,
    odometer_start_km: 24712.3,
    odometer_end_km: 24740.9,
    soc_start_percent: 66,
    soc_start_recorded_at: "2026-11-09T18:12:00Z",
    soc_end_percent: 61,
    soc_end_recorded_at: "2026-11-10T08:41:00Z",
    energy_kwh: 3.55,
    energy_kwh_uncertainty: 0.71,
    energy_source: "soc_inferred",
    usable_capacity_kwh: 71.0,
    capacity_source: "catalog",
    quality_flag: "low",
    quality_reasons: ["soc_delta_small", "soc_reading_stale", "start_time_bounded"],
    review_status: "needs_review",
    reviewed_at: null,
    in_progress: false,
    planned_trip_id: null,
    plan_leg_index: null,
    inference_version: "telemetry-v1",
    is_simulated: false,
    ...overrides,
  };
}

export { TRIP_ID };
