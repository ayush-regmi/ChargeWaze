import type { components } from "@chargewaze/api-client";

import { KM_PER_MILE, type UnitSystem } from "@/lib/units";

export type VehicleSpecs = components["schemas"]["VehicleSpecsOut"];
export type VehicleSpecsUpdate = components["schemas"]["VehicleSpecsUpdate"];
export type Connector = NonNullable<VehicleSpecsUpdate["connectors"]>[number];

/** Same rules as `PATCH /api/v1/vehicles/{id}/specs` (api-contracts §3.4a). */
export const CONNECTORS: readonly Connector[] = ["NACS", "CCS1", "J1772", "CHAdeMO"];
export const MAX_CAPACITY_KWH = 300;
export const MAX_POWER_KW = 1000;
export const MAX_CURVE_POINTS = 20;
/** kWh/km bounds and precision for `baseline_kwh_per_km` (api §3.4a). */
export const MAX_BASELINE_KWH_PER_KM = 1;
const BASELINE_DECIMALS = 3;

/**
 * Consumption is typed in the user's unit preference (phase4_plan F8):
 * Wh/mi for imperial, kWh/100 km for metric. The API speaks kWh/km.
 */
export function consumptionUnit(units: UnitSystem): string {
  return units === "imperial" ? "Wh/mi" : "kWh/100 km";
}

/** kWh/km → the user's unit, rounded for display (whole Wh/mi, 0.1 kWh/100 km). */
export function kwhPerKmToDisplay(kwhPerKm: number, units: UnitSystem): number {
  return units === "imperial"
    ? Math.round(kwhPerKm * 1000 * KM_PER_MILE)
    : Math.round(kwhPerKm * 1000) / 10;
}

/** The user's unit → kWh/km, rounded to the 3 decimals the API stores. */
export function displayToKwhPerKm(value: number, units: UnitSystem): number {
  const kwhPerKm = units === "imperial" ? value / 1000 / KM_PER_MILE : value / 100;
  const scale = 10 ** BASELINE_DECIMALS;
  return Math.round(kwhPerKm * scale) / scale;
}

/** "322 Wh/mi" / "20 kWh/100 km", or null when there's no value. */
export function formatConsumption(
  kwhPerKm: number | null | undefined,
  units: UnitSystem,
): string | null {
  if (kwhPerKm == null || !Number.isFinite(kwhPerKm)) return null;
  return `${kwhPerKmToDisplay(kwhPerKm, units)} ${consumptionUnit(units)}`;
}

/** One editable charging-curve row. Battery level is entered as a percentage. */
export type CurveRow = { socPercent: string; powerKw: string };

export type SpecsFormValues = {
  capacityKwh: string;
  /** In the user's consumption unit; empty means "use the catalog value". */
  consumption: string;
  connectors: Connector[];
  curve: CurveRow[];
};

export type SpecsFormErrors = {
  capacityKwh?: string;
  consumption?: string;
  connectors?: string;
  curve?: string;
  /** Per-row messages, same index as `curve`. */
  curveRows?: (string | undefined)[];
};

export type SpecsValidation =
  | { ok: true; body: Required<VehicleSpecsUpdate> }
  | { ok: false; errors: SpecsFormErrors };

export function specsToFormValues(
  specs: VehicleSpecs | null | undefined,
  units: UnitSystem,
): SpecsFormValues {
  return {
    capacityKwh: specs?.usable_capacity_kwh != null ? String(specs.usable_capacity_kwh) : "",
    consumption:
      specs?.baseline_kwh_per_km != null
        ? String(kwhPerKmToDisplay(specs.baseline_kwh_per_km, units))
        : "",
    connectors: (specs?.connectors ?? []).filter((c): c is Connector =>
      (CONNECTORS as readonly string[]).includes(c),
    ),
    curve: (specs?.charging_curve ?? []).map((p) => ({
      // Round away float noise like 0.29 * 100 = 28.999999999999996.
      socPercent: String(Math.round(p.soc * 10000) / 100),
      powerKw: String(p.power_kw),
    })),
  };
}

function parseNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : Number.NaN;
}

/**
 * Validate the form and build the full PATCH body. Mirrors the server's rules
 * so most mistakes are caught before a round trip; the server stays the
 * authority.
 */
export function validateSpecsForm(values: SpecsFormValues, units: UnitSystem): SpecsValidation {
  const errors: SpecsFormErrors = {};

  const capacity = parseNumber(values.capacityKwh);
  if (capacity !== null) {
    if (Number.isNaN(capacity)) {
      errors.capacityKwh = "Enter a number.";
    } else if (capacity <= 0 || capacity > MAX_CAPACITY_KWH) {
      errors.capacityKwh = `Must be more than 0 and at most ${MAX_CAPACITY_KWH} kWh.`;
    } else if (Math.round(capacity * 100) / 100 !== capacity) {
      errors.capacityKwh = "Use at most two decimal places.";
    }
  }

  const consumption = parseNumber(values.consumption);
  let baseline: number | null = null;
  if (consumption !== null) {
    if (Number.isNaN(consumption)) {
      errors.consumption = "Enter a number.";
    } else {
      baseline = displayToKwhPerKm(consumption, units);
      if (consumption <= 0 || baseline <= 0 || baseline > MAX_BASELINE_KWH_PER_KM) {
        const max = kwhPerKmToDisplay(MAX_BASELINE_KWH_PER_KM, units);
        errors.consumption = `Must be more than 0 and at most ${max} ${consumptionUnit(units)}.`;
      }
    }
  }

  if (new Set(values.connectors).size !== values.connectors.length) {
    errors.connectors = "Each connector can only be listed once.";
  }

  if (values.curve.length > MAX_CURVE_POINTS) {
    errors.curve = `At most ${MAX_CURVE_POINTS} points.`;
  }
  const points: { soc: number; power_kw: number }[] = [];
  const rowErrors: (string | undefined)[] = values.curve.map((row) => {
    const soc = parseNumber(row.socPercent);
    const power = parseNumber(row.powerKw);
    if (soc === null || power === null) return "Fill in both values.";
    if (Number.isNaN(soc) || Number.isNaN(power)) return "Enter numbers.";
    if (soc < 0 || soc > 100) return "Battery level must be 0–100%.";
    if (power <= 0 || power > MAX_POWER_KW) {
      return `Power must be more than 0 and at most ${MAX_POWER_KW} kW.`;
    }
    points.push({ soc: Math.round(soc * 100) / 10000, power_kw: power });
    return undefined;
  });
  if (rowErrors.some(Boolean)) {
    errors.curveRows = rowErrors;
  } else if (points.some((p, i) => i > 0 && p.soc <= points[i - 1].soc)) {
    errors.curve = "Battery levels must go up from one row to the next.";
  }

  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return {
    ok: true,
    body: {
      usable_capacity_kwh: capacity,
      connectors: [...values.connectors],
      charging_curve: points,
      baseline_kwh_per_km: baseline,
    },
  };
}
