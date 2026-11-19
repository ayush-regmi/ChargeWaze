"use client";

import { useId, useState, useTransition } from "react";

import { updateVehicleSpecs } from "@/app/(app)/garage/actions";
import { Button } from "@/components/ui/button";
import type { UnitSystem } from "@/lib/units";
import {
  CONNECTORS,
  MAX_CAPACITY_KWH,
  MAX_CURVE_POINTS,
  consumptionUnit,
  kwhPerKmToDisplay,
  specsToFormValues,
  validateSpecsForm,
  type Connector,
  type SpecsFormErrors,
  type SpecsFormValues,
  type VehicleSpecs,
} from "@/lib/vehicle-specs";

import { SpecsPanel } from "./specs-panel";

/**
 * Specs view with an Edit mode (api §3.4a). Saving calls the server action,
 * which revalidates the page so the read-only view shows the stored values.
 */
export function SpecsEditor({
  vehicleId,
  specs,
  units,
}: {
  vehicleId: string;
  specs: VehicleSpecs | null;
  units: UnitSystem;
}) {
  const [editing, setEditing] = useState(false);

  if (!editing) {
    return (
      <SpecsPanel
        specs={specs}
        units={units}
        action={
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            Edit specs
          </Button>
        }
      />
    );
  }
  return (
    <SpecsForm vehicleId={vehicleId} specs={specs} units={units} onDone={() => setEditing(false)} />
  );
}

const inputClass =
  "w-full rounded-md border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-950 aria-[invalid=true]:border-red-500";
const errorClass = "text-xs text-red-700 dark:text-red-400";

function SpecsForm({
  vehicleId,
  specs,
  units,
  onDone,
}: {
  vehicleId: string;
  specs: VehicleSpecs | null;
  units: UnitSystem;
  onDone: () => void;
}) {
  const [values, setValues] = useState<SpecsFormValues>(() => specsToFormValues(specs, units));
  const catalogBaseline = specs?.catalog_baseline_kwh_per_km;
  const catalogDisplay =
    catalogBaseline != null ? String(kwhPerKmToDisplay(catalogBaseline, units)) : undefined;
  const [errors, setErrors] = useState<SpecsFormErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const ids = useId();

  function update(patch: Partial<SpecsFormValues>) {
    setValues((v) => ({ ...v, ...patch }));
  }

  function toggleConnector(c: Connector, checked: boolean) {
    const next = checked
      ? CONNECTORS.filter((x) => x === c || values.connectors.includes(x))
      : values.connectors.filter((x) => x !== c);
    update({ connectors: next });
  }

  function setRow(i: number, patch: Partial<SpecsFormValues["curve"][number]>) {
    update({ curve: values.curve.map((row, j) => (j === i ? { ...row, ...patch } : row)) });
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);
    const result = validateSpecsForm(values, units);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    startTransition(async () => {
      const res = await updateVehicleSpecs(vehicleId, result.body);
      if (res?.error) setServerError(res.error);
      else onDone();
    });
  }

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      aria-label="Edit specs"
      className="space-y-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
      <h2 className="text-sm font-medium">Edit specs</h2>

      <div className="space-y-1">
        <label htmlFor={`${ids}-capacity`} className="text-sm">
          Usable battery capacity (kWh)
        </label>
        <input
          id={`${ids}-capacity`}
          inputMode="decimal"
          className={`${inputClass} max-w-40`}
          value={values.capacityKwh}
          onChange={(e) => update({ capacityKwh: e.target.value })}
          aria-invalid={errors.capacityKwh ? true : undefined}
          aria-describedby={`${ids}-capacity-help`}
        />
        <p id={`${ids}-capacity-help`} className="text-xs text-neutral-500 dark:text-neutral-400">
          Leave empty if unknown. Up to {MAX_CAPACITY_KWH} kWh.
        </p>
        {errors.capacityKwh ? (
          <p role="alert" className={errorClass}>
            {errors.capacityKwh}
          </p>
        ) : null}
      </div>

      <div className="space-y-1">
        <label htmlFor={`${ids}-consumption`} className="text-sm">
          Consumption ({consumptionUnit(units)})
        </label>
        <input
          id={`${ids}-consumption`}
          inputMode="decimal"
          className={`${inputClass} max-w-40`}
          value={values.consumption}
          placeholder={catalogDisplay}
          onChange={(e) => update({ consumption: e.target.value })}
          aria-invalid={errors.consumption ? true : undefined}
          aria-describedby={`${ids}-consumption-help`}
        />
        <p
          id={`${ids}-consumption-help`}
          className="text-xs text-neutral-500 dark:text-neutral-400"
        >
          {catalogDisplay
            ? `Leave empty to use the catalog's highway estimate (${catalogDisplay} ${consumptionUnit(units)}).`
            : "Your typical highway consumption. Trip planning needs it for this vehicle."}
        </p>
        {errors.consumption ? (
          <p role="alert" className={errorClass}>
            {errors.consumption}
          </p>
        ) : null}
      </div>

      <fieldset className="space-y-1">
        <legend className="text-sm">Connectors (port and adapters you carry)</legend>
        <div className="flex flex-wrap gap-4">
          {CONNECTORS.map((c) => (
            <label key={c} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={values.connectors.includes(c)}
                onChange={(e) => toggleConnector(c, e.target.checked)}
              />
              {c}
            </label>
          ))}
        </div>
        {errors.connectors ? (
          <p role="alert" className={errorClass}>
            {errors.connectors}
          </p>
        ) : null}
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-sm">Charging curve</legend>
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Max charging power at each battery level, lowest level first.
        </p>
        {values.curve.length > 0 ? (
          <table className="text-sm">
            <thead>
              <tr className="text-left text-neutral-500 dark:text-neutral-400">
                <th className="pr-3 font-normal">Battery (%)</th>
                <th className="pr-3 font-normal">Max power (kW)</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {values.curve.map((row, i) => {
                const rowError = errors.curveRows?.[i];
                return (
                  <tr key={i} className="align-top">
                    <td className="pr-3 pb-2">
                      <input
                        aria-label={`Row ${i + 1} battery level (%)`}
                        inputMode="decimal"
                        className={`${inputClass} w-24`}
                        value={row.socPercent}
                        onChange={(e) => setRow(i, { socPercent: e.target.value })}
                        aria-invalid={rowError ? true : undefined}
                      />
                    </td>
                    <td className="pr-3 pb-2">
                      <input
                        aria-label={`Row ${i + 1} max power (kW)`}
                        inputMode="decimal"
                        className={`${inputClass} w-24`}
                        value={row.powerKw}
                        onChange={(e) => setRow(i, { powerKw: e.target.value })}
                        aria-invalid={rowError ? true : undefined}
                      />
                      {rowError ? (
                        <p role="alert" className={`${errorClass} mt-1`}>
                          {rowError}
                        </p>
                      ) : null}
                    </td>
                    <td className="pb-2">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        aria-label={`Remove row ${i + 1}`}
                        onClick={() => update({ curve: values.curve.filter((_, j) => j !== i) })}
                      >
                        Remove
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : null}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={values.curve.length >= MAX_CURVE_POINTS}
          onClick={() => update({ curve: [...values.curve, { socPercent: "", powerKw: "" }] })}
        >
          Add point
        </Button>
        {errors.curve ? (
          <p role="alert" className={errorClass}>
            {errors.curve}
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
          {pending ? "Saving…" : "Save specs"}
        </Button>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
