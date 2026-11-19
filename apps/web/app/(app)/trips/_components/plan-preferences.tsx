"use client";

import { useId, useState } from "react";

import {
  preferencesToFormValues,
  validatePreferencesForm,
  type Preferences,
  type PreferencesFormErrors,
} from "@/components/profile/preferences";
import { Button } from "@/components/ui/button";
import {
  changedPlanPreferences,
  parseSocPercent,
  type PlanPreferences,
  type PlanRequestSettings,
} from "@/lib/plan-format";

export type ReplanRequest = {
  preferences: PlanPreferences | null;
  /** A starting charge the user typed, or null to use the car's reading. */
  startingSoc: number | null;
};

type Errors = PreferencesFormErrors & { startingSoc?: string };

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium">
        {label}
      </label>
      {children}
      {hint ? <p className="text-xs text-neutral-500">{hint}</p> : null}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-xs text-red-700 dark:text-red-300">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Per-request keys that are set (the API never gets explicit nulls). */
function withoutNulls(p: PlanPreferences | null): Partial<Preferences> {
  const out: Partial<Preferences> = {};
  if (!p) return out;
  if (p.reserve_soc != null) out.reserve_soc = p.reserve_soc;
  if (p.preferred_charge_to != null) out.preferred_charge_to = p.preferred_charge_to;
  if (p.max_detour_minutes != null) out.max_detour_minutes = p.max_detour_minutes;
  if (p.preferred_networks != null) out.preferred_networks = p.preferred_networks;
  return out;
}

const INPUT =
  "w-full rounded-md border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900";

/**
 * "Re-plan with different preferences" (phase4_plan F6). Starts from the
 * stored preferences; only the keys that differ are sent as the request's
 * `preferences`, so the stored ones stay unchanged (edit them on Profile).
 * Validation is the Profile editor's (api §3.1a). `initial` (the settings of
 * the plan on screen) prefills the form, so it shows what that plan used.
 */
export function PlanPreferencesPanel({
  stored,
  initial: settings,
  pending,
  onReplan,
}: {
  stored: Preferences;
  initial: PlanRequestSettings;
  pending: boolean;
  onReplan: (request: ReplanRequest) => void;
}) {
  const id = useId();
  const initial = preferencesToFormValues({ ...stored, ...withoutNulls(settings.preferences) });
  const [reserve, setReserve] = useState(initial.reservePercent);
  const [chargeTo, setChargeTo] = useState(initial.chargeToPercent);
  const [detour, setDetour] = useState(initial.maxDetourMinutes);
  const [networks, setNetworks] = useState(initial.networks.join(", "));
  const [startingSoc, setStartingSoc] = useState(
    settings.startingSoc === null ? "" : String(Math.round(settings.startingSoc * 100)),
  );
  const [errors, setErrors] = useState<Errors>({});

  const submit = () => {
    const result = validatePreferencesForm({
      units: stored.units,
      reservePercent: reserve,
      chargeToPercent: chargeTo,
      maxDetourMinutes: detour,
      networks: networks
        .split(",")
        .map((n) => n.trim())
        .filter(Boolean),
    });
    const next: Errors = result.ok ? {} : { ...result.errors };
    let soc: number | null = null;
    if (startingSoc.trim() !== "") {
      const parsed = parseSocPercent(startingSoc);
      if ("error" in parsed) next.startingSoc = parsed.error;
      else soc = parsed.value;
    }
    setErrors(next);
    if (!result.ok || Object.keys(next).length > 0) return;
    onReplan({ preferences: changedPlanPreferences(result.body, stored), startingSoc: soc });
  };

  const described = (key: keyof Errors, field: string) =>
    errors[key] ? { "aria-invalid": true, "aria-describedby": `${field}-error` } : {};

  return (
    <details className="rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800">
      <summary className="cursor-pointer font-medium">Re-plan with different preferences</summary>
      <form
        aria-label="Re-plan with different preferences"
        className="mt-3 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="grid grid-cols-2 gap-3">
          <Field id={`${id}-reserve`} label="Reserve (%)" error={errors.reservePercent}>
            <input
              id={`${id}-reserve`}
              inputMode="decimal"
              value={reserve}
              onChange={(e) => setReserve(e.target.value)}
              className={INPUT}
              {...described("reservePercent", `${id}-reserve`)}
            />
          </Field>
          <Field id={`${id}-charge-to`} label="Charge to (%)" error={errors.chargeToPercent}>
            <input
              id={`${id}-charge-to`}
              inputMode="decimal"
              value={chargeTo}
              onChange={(e) => setChargeTo(e.target.value)}
              className={INPUT}
              {...described("chargeToPercent", `${id}-charge-to`)}
            />
          </Field>
          <Field id={`${id}-detour`} label="Max detour (min)" error={errors.maxDetourMinutes}>
            <input
              id={`${id}-detour`}
              inputMode="numeric"
              value={detour}
              onChange={(e) => setDetour(e.target.value)}
              className={INPUT}
              {...described("maxDetourMinutes", `${id}-detour`)}
            />
          </Field>
          <Field
            id={`${id}-soc`}
            label="Starting battery (%)"
            hint="Leave empty to use your car's reading."
            error={errors.startingSoc}
          >
            <input
              id={`${id}-soc`}
              inputMode="numeric"
              value={startingSoc}
              onChange={(e) => setStartingSoc(e.target.value)}
              className={INPUT}
              {...described("startingSoc", `${id}-soc`)}
            />
          </Field>
        </div>
        <Field
          id={`${id}-networks`}
          label="Preferred networks"
          hint="Comma-separated. Other networks are used only if no plan works without them."
          error={errors.networks}
        >
          <input
            id={`${id}-networks`}
            value={networks}
            onChange={(e) => setNetworks(e.target.value)}
            className={INPUT}
            {...described("networks", `${id}-networks`)}
          />
        </Field>
        <p className="text-xs text-neutral-500">
          Applies to this plan only. Change your saved preferences on the Profile page.
        </p>
        <Button type="submit" size="sm" disabled={pending}>
          Re-plan
        </Button>
      </form>
    </details>
  );
}
