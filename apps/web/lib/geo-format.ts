/**
 * Geographic helpers for display: distances in the user's units (through
 * `lib/units.ts`) and great-circle distance between two points.
 */

import { formatDistance, kmToMiles, type UnitSystem } from "@/lib/units";

export type Point = { lat: number; lon: number };

const EARTH_RADIUS_KM = 6371.0088;

/** Great-circle (haversine) distance in km. */
export function haversineKm(a: Point, b: Point): number {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

/**
 * A distance as one display string, e.g. "3.2 mi" or "48 km". Under 10 units
 * keeps one decimal. Null stays null (never guessed).
 */
export function formatDistanceText(
  km: number | null | undefined,
  units: UnitSystem,
): string | null {
  if (km === null || km === undefined || !Number.isFinite(km)) return null;
  const inUnit = units === "imperial" ? kmToMiles(km) : km;
  const q = formatDistance(km, units, { maximumFractionDigits: inUnit < 10 ? 1 : 0 });
  return q ? `${q.value} ${q.unit}` : null;
}

/** The unit label for distance inputs (radius, corridor). */
export function distanceUnit(units: UnitSystem): "mi" | "km" {
  return units === "imperial" ? "mi" : "km";
}

/**
 * Round a coordinate for URLs. Four decimals is about 11 m, plenty for a
 * search centre, and keeps shared links from carrying a precise position.
 */
export function roundCoord(value: number, decimals = 4): number {
  const f = 10 ** decimals;
  return Math.round(value * f) / f;
}
