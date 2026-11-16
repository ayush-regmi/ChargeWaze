/**
 * Unit conversion and display formatting for vehicle quantities.
 *
 * The API always speaks metric with the unit in the field name (`range_km`,
 * `odometer_km`); converting to the user's display preference happens here
 * (docs/api-contracts.md §1).
 */

export type UnitSystem = "imperial" | "metric";

export const KM_PER_MILE = 1.609344;

export function kmToMiles(km: number): number {
  return km / KM_PER_MILE;
}

export function milesToKm(miles: number): number {
  return miles * KM_PER_MILE;
}

export type Quantity = { value: string; unit: string };

/** A distance in km, shown in the user's units. Null stays null (never guessed). */
export function formatDistance(
  km: number | null | undefined,
  units: UnitSystem,
  options: { maximumFractionDigits?: number } = {},
): Quantity | null {
  if (km === null || km === undefined || !Number.isFinite(km)) return null;
  const value = units === "imperial" ? kmToMiles(km) : km;
  return {
    value: value.toLocaleString(undefined, {
      maximumFractionDigits: options.maximumFractionDigits ?? 0,
    }),
    unit: units === "imperial" ? "mi" : "km",
  };
}

export function formatPercent(percent: number | null | undefined): Quantity | null {
  if (percent === null || percent === undefined || !Number.isFinite(percent)) return null;
  return { value: Math.round(percent).toString(), unit: "%" };
}

export function formatPowerKw(kw: number | null | undefined): Quantity | null {
  if (kw === null || kw === undefined || !Number.isFinite(kw)) return null;
  return { value: kw.toLocaleString(undefined, { maximumFractionDigits: 1 }), unit: "kW" };
}
