import type { components } from "@chargewaze/api-client";

export type VehicleSummary = components["schemas"]["VehicleSummary"];
export type VehicleDetail = components["schemas"]["VehicleDetail"];

/**
 * Display names for ChargeWaze signal codes (see SIGNAL_MAP in
 * services/api/app/integrations/smartcar/schemas.py). Unknown codes fall back
 * to the code itself so a new backend signal still shows up.
 */
const SIGNAL_LABELS: Record<string, string> = {
  soc: "Battery level",
  range: "Range",
  odometer: "Odometer",
  charging_status: "Charging",
  charging_status_detail: "Charging detail",
  charging_power: "Charging power",
  charge_limit: "Charge limit",
  cable_connected: "Cable connected",
};

export function signalLabel(code: string): string {
  return Object.hasOwn(SIGNAL_LABELS, code) ? SIGNAL_LABELS[code] : code;
}

export function vehicleTitle(v: Pick<VehicleSummary, "make" | "model" | "year">): string {
  return `${v.year} ${v.make} ${v.model}`;
}

export type Freshness = "awaiting" | "stale" | "fresh";

/** "awaiting" until the first webhook lands (F0: can take minutes after Connect). */
export function freshness(
  v: Pick<VehicleSummary, "last_state_recorded_at" | "is_stale">,
): Freshness {
  if (!v.last_state_recorded_at) return "awaiting";
  return v.is_stale ? "stale" : "fresh";
}

export type VehicleState = components["schemas"]["VehicleState"];

/**
 * The vehicle the Dashboard shows: the user's primary vehicle if it is still
 * connected, otherwise the most recently connected one (phase2_plan.md D6).
 * `vehicles` is the `GET /api/v1/vehicles` list, already newest first.
 */
export function pickDashboardVehicle<T extends Pick<VehicleSummary, "id" | "connection_status">>(
  vehicles: readonly T[],
  primaryVehicleId: string | null | undefined,
): T | null {
  const connected = vehicles.filter((v) => v.connection_status === "connected");
  if (primaryVehicleId) {
    const primary = connected.find((v) => v.id === primaryVehicleId);
    if (primary) return primary;
  }
  return connected[0] ?? null;
}
