import type { components } from "@chargewise/api-client";

export type VehicleSpecs = components["schemas"]["VehicleSpecsOut"];
export type VehicleSpecsUpdate = components["schemas"]["VehicleSpecsUpdate"];
export type Connector = NonNullable<VehicleSpecsUpdate["connectors"]>[number];

/** Same rules as `PATCH /api/v1/vehicles/{id}/specs` (api-contracts §3.4a). */
export const CONNECTORS: readonly Connector[] = ["NACS", "CCS1", "J1772", "CHAdeMO"];
export const MAX_CAPACITY_KWH = 300;
export const MAX_POWER_KW = 1000;
export const MAX_CURVE_POINTS = 20;

/** One editable charging-curve row. Battery level is entered as a percentage. */
export type CurveRow = { socPercent: string; powerKw: string };

export type SpecsFormValues = {
  capacityKwh: string;
  connectors: Connector[];
  curve: CurveRow[];
};

export type SpecsFormErrors = {
  capacityKwh?: string;
  connectors?: string;
  curve?: string;
  /** Per-row messages, same index as `curve`. */
  curveRows?: (string | undefined)[];
};

export type SpecsValidation =
  | { ok: true; body: Required<VehicleSpecsUpdate> }
  | { ok: false; errors: SpecsFormErrors };

export function specsToFormValues(specs: VehicleSpecs | null | undefined): SpecsFormValues {
  return {
    capacityKwh: specs?.usable_capacity_kwh != null ? String(specs.usable_capacity_kwh) : "",
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
export function validateSpecsForm(values: SpecsFormValues): SpecsValidation {
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
    },
  };
}
