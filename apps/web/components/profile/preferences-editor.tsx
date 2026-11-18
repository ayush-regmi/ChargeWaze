"use client";

import { useId, useState, useTransition } from "react";

import { updatePreferences } from "@/app/(app)/profile/actions";
import { Button } from "@/components/ui/button";
import type { UnitSystem } from "@/lib/units";

import {
  MAX_DETOUR_MINUTES,
  MAX_NETWORKS,
  fractionToPercent,
  networkNameError,
  preferencesToFormValues,
  validatePreferencesForm,
  type Preferences,
  type PreferencesFormErrors,
  type PreferencesFormValues,
} from "./preferences";

const UNIT_LABELS: Record<UnitSystem, string> = {
  imperial: "Miles, °F",
  metric: "Kilometers, °C",
};

/**
 * Profile preferences with an Edit mode (api §3.1a, PRD §4.6). Saving calls
 * the server action, which revalidates Profile and Dashboard so every screen
 * renders with the stored values.
 */
export function PreferencesEditor({ preferences }: { preferences: Preferences }) {
  const [current, setCurrent] = useState(preferences);
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);

  if (editing) {
    return (
      <PreferencesForm
        preferences={current}
        onCancel={() => setEditing(false)}
        onSaved={(next) => {
          setCurrent(next);
          setEditing(false);
          setSaved(true);
        }}
      />
    );
  }

  const networks = current.preferred_networks ?? [];
  return (
    <section
      aria-label="Preferences"
      className="space-y-4 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Preferences</h2>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setSaved(false);
            setEditing(true);
          }}
        >
          Edit preferences
        </Button>
      </div>
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Units" value={UNIT_LABELS[current.units]} />
        <Field label="Reserve at destination" value={`${fractionToPercent(current.reserve_soc)}%`} />
        <Field
          label="Charge to"
          value={`${fractionToPercent(current.preferred_charge_to)}%`}
        />
        <Field label="Max charging detour" value={`${current.max_detour_minutes} minutes`} />
        <Field
          label="Preferred networks"
          value={networks.length > 0 ? networks.join(", ") : "Any network"}
          wide
        />
      </dl>
      {saved ? (
        <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">
          Preferences saved.
        </p>
      ) : null}
    </section>
  );
}

function Field({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <dt className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        {label}
      </dt>
      <dd className="mt-1 text-sm">{value}</dd>
    </div>
  );
}

const inputClass =
  "w-full rounded-md border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-950 aria-[invalid=true]:border-red-500";
const helpClass = "text-xs text-neutral-500 dark:text-neutral-400";
const errorClass = "text-xs text-red-700 dark:text-red-400";

function PreferencesForm({
  preferences,
  onCancel,
  onSaved,
}: {
  preferences: Preferences;
  onCancel: () => void;
  onSaved: (next: Preferences) => void;
}) {
  const [values, setValues] = useState<PreferencesFormValues>(() =>
    preferencesToFormValues(preferences),
  );
  const [errors, setErrors] = useState<PreferencesFormErrors>({});
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const ids = useId();
  const networksError = draftError ?? errors.networks;

  function update(patch: Partial<PreferencesFormValues>) {
    setValues((v) => ({ ...v, ...patch }));
  }

  function addNetwork() {
    const problem = networkNameError(values.networks, draft);
    if (problem) {
      setDraftError(problem);
      return;
    }
    update({ networks: [...values.networks, draft.trim()] });
    setDraft("");
    setDraftError(null);
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);
    const result = validatePreferencesForm(values);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    startTransition(async () => {
      const res = await updatePreferences(result.body);
      // Updates after an `await` are no longer part of the transition, so
      // without this wrapper the error renders while `pending` is still true
      // ("Saving…" button) and `pending` flips in a later commit. Wrapping
      // them makes the result and the end of saving commit together.
      startTransition(() => {
        if ("error" in res) setServerError(res.error);
        else onSaved(res.preferences);
      });
    });
  }

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      aria-label="Edit preferences"
      className="space-y-4 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
      <h2 className="text-sm font-medium">Edit preferences</h2>

      <fieldset className="space-y-1">
        <legend className="text-sm">Units</legend>
        <div className="flex flex-wrap gap-4">
          {(Object.keys(UNIT_LABELS) as UnitSystem[]).map((u) => (
            <label key={u} className="flex items-center gap-1.5 text-sm">
              <input
                type="radio"
                name={`${ids}-units`}
                value={u}
                checked={values.units === u}
                onChange={() => update({ units: u })}
              />
              {UNIT_LABELS[u]}
            </label>
          ))}
        </div>
      </fieldset>

      <NumberField
        id={`${ids}-reserve`}
        label="Reserve at destination (%)"
        help="Lowest battery level you want to arrive with."
        value={values.reservePercent}
        error={errors.reservePercent}
        onChange={(v) => update({ reservePercent: v })}
      />
      <NumberField
        id={`${ids}-charge-to`}
        label="Charge to (%)"
        help="Battery level to charge up to at each stop."
        value={values.chargeToPercent}
        error={errors.chargeToPercent}
        onChange={(v) => update({ chargeToPercent: v })}
      />
      <NumberField
        id={`${ids}-detour`}
        label="Max charging detour (minutes)"
        help={`0 to ${MAX_DETOUR_MINUTES} minutes off the route to reach a charger.`}
        value={values.maxDetourMinutes}
        error={errors.maxDetourMinutes}
        onChange={(v) => update({ maxDetourMinutes: v })}
        inputMode="numeric"
      />

      <fieldset className="space-y-2">
        <legend className="text-sm">Preferred networks</legend>
        <p className={helpClass}>Leave empty to use any network. Up to {MAX_NETWORKS}.</p>
        {values.networks.length > 0 ? (
          <ul className="flex flex-wrap gap-2">
            {values.networks.map((name, i) => (
              <li
                key={`${name}-${i}`}
                className="flex items-center gap-1 rounded-md bg-neutral-100 py-0.5 pl-2 text-sm dark:bg-neutral-800"
              >
                {name}
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Remove ${name}`}
                  onClick={() => update({ networks: values.networks.filter((_, j) => j !== i) })}
                >
                  ×
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="flex max-w-sm gap-2">
          <input
            aria-label="Network name"
            className={inputClass}
            value={draft}
            placeholder="e.g. EVgo"
            onChange={(e) => {
              setDraft(e.target.value);
              setDraftError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addNetwork();
              }
            }}
            aria-invalid={draftError ? true : undefined}
          />
          <Button type="button" variant="outline" size="sm" onClick={addNetwork}>
            Add
          </Button>
        </div>
        {networksError ? (
          <p role="alert" className={errorClass}>
            {networksError}
          </p>
        ) : null}
      </fieldset>

      {serverError ? (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {serverError}
        </p>
      ) : null}

      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : "Save preferences"}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function NumberField({
  id,
  label,
  help,
  value,
  error,
  onChange,
  inputMode = "decimal",
}: {
  id: string;
  label: string;
  help: string;
  value: string;
  error?: string;
  onChange: (value: string) => void;
  inputMode?: "decimal" | "numeric";
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm">
        {label}
      </label>
      <input
        id={id}
        inputMode={inputMode}
        className={`${inputClass} max-w-40`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={`${id}-help`}
      />
      <p id={`${id}-help`} className={helpClass}>
        {help}
      </p>
      {error ? (
        <p role="alert" className={errorClass}>
          {error}
        </p>
      ) : null}
    </div>
  );
}
