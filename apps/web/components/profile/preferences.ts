import type { components } from "@chargewaze/api-client";

import type { UnitSystem } from "@/lib/units";

export type Preferences = components["schemas"]["UserPreferencesOut"];

/** The keys this editor sends. `primary_vehicle_id` is managed in the Garage. */
export type PreferencesBody = {
  units: UnitSystem;
  reserve_soc: number;
  preferred_charge_to: number;
  max_detour_minutes: number;
  preferred_networks: string[];
};

/** Same rules as `PATCH /api/v1/me/preferences` (api-contracts §3.1a). */
export const MAX_DETOUR_MINUTES = 240;
export const MAX_NETWORKS = 20;
export const MAX_NETWORK_NAME_LENGTH = 100;

export type PreferencesFormValues = {
  units: UnitSystem;
  /** Percentages as typed (the API stores fractions with 3 decimals = 0.1%). */
  reservePercent: string;
  chargeToPercent: string;
  maxDetourMinutes: string;
  networks: string[];
};

export type PreferencesFormErrors = {
  reservePercent?: string;
  chargeToPercent?: string;
  maxDetourMinutes?: string;
  networks?: string;
};

export type PreferencesValidation =
  | { ok: true; body: PreferencesBody }
  | { ok: false; errors: PreferencesFormErrors };

/** Fraction → percent string, rounding away float noise (0.29 * 100). */
export function fractionToPercent(fraction: number): string {
  return String(Math.round(fraction * 1000) / 10);
}

export function preferencesToFormValues(prefs: Preferences): PreferencesFormValues {
  return {
    units: prefs.units,
    reservePercent: fractionToPercent(prefs.reserve_soc),
    chargeToPercent: fractionToPercent(prefs.preferred_charge_to),
    maxDetourMinutes: String(prefs.max_detour_minutes),
    networks: [...(prefs.preferred_networks ?? [])],
  };
}

const DECIMAL_RE = /^(\d+)(?:\.(\d*))?$|^\.(\d+)$/;

/** Parse a 0–100 percentage with at most one decimal into a 3-decimal fraction. */
function parsePercent(raw: string): { value: number } | { error: string } {
  const trimmed = raw.trim();
  if (trimmed === "") return { error: "Enter a percentage." };
  const match = DECIMAL_RE.exec(trimmed);
  if (!match) return { error: "Enter a number." };
  const decimals = match[2] ?? match[3] ?? "";
  if (decimals.length > 1) return { error: "Use at most one decimal place." };
  const percent = Number(trimmed);
  if (percent < 0 || percent > 100) return { error: "Must be between 0 and 100%." };
  // Integer tenths of a percent, then one division: 15 → 150 / 1000 = 0.15.
  return { value: Math.round(percent * 10) / 1000 };
}

/**
 * Reason a network name can't be added to `list`, or null if it can. Mirrors
 * the server: trimmed, non-empty, ≤ 100 chars, distinct ignoring case, ≤ 20.
 */
export function networkNameError(list: readonly string[], name: string): string | null {
  const trimmed = name.trim();
  if (!trimmed) return "Enter a network name.";
  if (trimmed.length > MAX_NETWORK_NAME_LENGTH) {
    return `Network names can be at most ${MAX_NETWORK_NAME_LENGTH} characters.`;
  }
  if (list.some((n) => n.trim().toLocaleLowerCase() === trimmed.toLocaleLowerCase())) {
    return `${trimmed} is already in the list.`;
  }
  if (list.length >= MAX_NETWORKS) return `At most ${MAX_NETWORKS} networks.`;
  return null;
}

/**
 * Validate the form and build the PATCH body. Catches what the server would
 * reject so most mistakes never make a round trip; the server stays the
 * authority.
 */
export function validatePreferencesForm(values: PreferencesFormValues): PreferencesValidation {
  const errors: PreferencesFormErrors = {};

  const reserve = parsePercent(values.reservePercent);
  if ("error" in reserve) errors.reservePercent = reserve.error;
  const chargeTo = parsePercent(values.chargeToPercent);
  if ("error" in chargeTo) errors.chargeToPercent = chargeTo.error;
  if ("value" in reserve && "value" in chargeTo && reserve.value >= chargeTo.value) {
    errors.reservePercent = "Reserve must be lower than the charge-to limit.";
  }

  const detourRaw = values.maxDetourMinutes.trim();
  const detour = Number(detourRaw);
  if (!/^\d+$/.test(detourRaw)) {
    errors.maxDetourMinutes = "Enter a whole number of minutes.";
  } else if (detour > MAX_DETOUR_MINUTES) {
    errors.maxDetourMinutes = `Must be between 0 and ${MAX_DETOUR_MINUTES} minutes.`;
  }

  const networks: string[] = [];
  for (const name of values.networks) {
    const problem = networkNameError(networks, name);
    if (problem) {
      errors.networks = problem;
      break;
    }
    networks.push(name.trim());
  }

  if (Object.keys(errors).length > 0 || !("value" in reserve) || !("value" in chargeTo)) {
    return { ok: false, errors };
  }
  return {
    ok: true,
    body: {
      units: values.units,
      reserve_soc: reserve.value,
      preferred_charge_to: chargeTo.value,
      max_detour_minutes: detour,
      preferred_networks: networks,
    },
  };
}
