/**
 * Display helpers for the Analytics page (phase5_plan F6). The API speaks
 * raw units (km, kWh, kWh/km; api-contracts §3.11); conversion to the user's
 * units happens only here. Null stays null: missing data is never guessed.
 */

import type { components } from "@chargewise/api-client";

import { kmToMiles, KM_PER_MILE, type UnitSystem } from "@/lib/units";

export type ConsumptionResponse = components["schemas"]["ConsumptionResponse"];
export type ConsumptionBucket = components["schemas"]["ConsumptionBucket"];
export type ConsumptionMetrics = components["schemas"]["ConsumptionMetrics"];
export type AnalyticsTrip = components["schemas"]["AnalyticsTrip"];
export type GroupBy = ConsumptionResponse["group_by"];

export const MISSING = "—";

/** Wh/mi for imperial, kWh/100 km for metric. */
export function consumptionUnit(units: UnitSystem): "Wh/mi" | "kWh/100 km" {
  return units === "imperial" ? "Wh/mi" : "kWh/100 km";
}

/** kWh/km → Wh/mi (imperial) or kWh/100 km (metric). */
export function toConsumption(
  kwhPerKm: number | null | undefined,
  units: UnitSystem,
): number | null {
  if (kwhPerKm === null || kwhPerKm === undefined || !Number.isFinite(kwhPerKm))
    return null;
  return units === "imperial" ? kwhPerKm * 1000 * KM_PER_MILE : kwhPerKm * 100;
}

/** "312 Wh/mi" or "19.4 kWh/100 km"; null → null. */
export function formatConsumption(
  kwhPerKm: number | null | undefined,
  units: UnitSystem,
): string | null {
  const value = toConsumption(kwhPerKm, units);
  if (value === null) return null;
  const digits = units === "imperial" ? 0 : 1;
  return `${value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${consumptionUnit(units)}`;
}

/** km → the user's distance unit, as a number for charts. */
export function toDistance(km: number, units: UnitSystem): number {
  return units === "imperial" ? kmToMiles(km) : km;
}

export function distanceUnitLabel(units: UnitSystem): "mi" | "km" {
  return units === "imperial" ? "mi" : "km";
}

/** "12.3 mi"; null → null. */
export function formatDistanceValue(
  km: number | null | undefined,
  units: UnitSystem,
): string | null {
  if (km === null || km === undefined || !Number.isFinite(km)) return null;
  const value = toDistance(km, units);
  return `${value.toLocaleString(undefined, { maximumFractionDigits: value < 100 ? 1 : 0 })} ${distanceUnitLabel(units)}`;
}

/** "36.21 kWh"; null → null. */
export function formatKwh(
  kwh: number | null | undefined,
  digits = 2,
): string | null {
  if (kwh === null || kwh === undefined || !Number.isFinite(kwh)) return null;
  return `${kwh.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })} kWh`;
}

/** A signed error such as "+0.51 kWh" or "−0.51 kWh"; null → null. */
export function formatSignedKwh(
  kwh: number | null | undefined,
  digits = 2,
): string | null {
  if (kwh === null || kwh === undefined || !Number.isFinite(kwh)) return null;
  const abs = Math.abs(kwh).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  if (Math.round(Math.abs(kwh) * 10 ** digits) === 0) return `${abs} kWh`;
  return `${kwh < 0 ? "−" : "+"}${abs} kWh`;
}

export type QualityFlag = "high" | "medium" | "low";

export function qualityFlag(value: string): QualityFlag {
  return value === "high" || value === "medium" ? value : "low";
}

export const QUALITY_LABELS: Record<QualityFlag, string> = {
  high: "High quality",
  medium: "Medium quality",
  low: "Low quality",
};

/** Human text for each D8 reason code. Unknown codes fall back to the code itself. */
const QUALITY_REASON_LABELS: Record<string, string> = {
  soc_missing: "Battery level missing at the start or end",
  soc_increased: "Battery level rose during the drive",
  capacity_unknown: "Battery capacity unknown",
  soc_delta_small: "Used under 10 % of the battery, so rounding matters",
  soc_reading_stale: "Battery reading more than 15 minutes from the drive",
  start_time_bounded: "Exact start time unknown",
  odometer_regression: "Odometer went backwards",
};

export function qualityReasonLabel(code: string): string {
  return Object.hasOwn(QUALITY_REASON_LABELS, code)
    ? QUALITY_REASON_LABELS[code]
    : code;
}

const REVIEW_LABELS: Record<string, string> = {
  auto: "Automatic",
  needs_review: "Needs review",
  confirmed: "Confirmed",
  rejected: "Rejected",
};

export function reviewLabel(status: string): string {
  return Object.hasOwn(REVIEW_LABELS, status) ? REVIEW_LABELS[status] : status;
}

/**
 * Where a leg's "Needs review" link goes: the matched plan's trip page, else
 * the saved-trips page (where P5-F7 lists observed drives to confirm).
 */
export function reviewHref(
  trip: Pick<AnalyticsTrip, "planned_trip_id">,
): string {
  return trip.planned_trip_id
    ? `/trips/${trip.planned_trip_id}`
    : "/trips/saved";
}

const GROUP_LABELS: Record<GroupBy, string> = {
  day: "Day",
  week: "Week",
  month: "Month",
};

export function groupLabel(groupBy: GroupBy): string {
  return GROUP_LABELS[groupBy];
}
