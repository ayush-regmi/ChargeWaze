"use client";

import { useId } from "react";

import { cn } from "@/lib/utils";
import {
  CONNECTOR_TYPES,
  MIN_POWER_OPTIONS,
  connectorLabel,
  defaultRadiusKm,
  effectiveConnectors,
  radiusOptions,
  toggleConnector,
  type ChargingFilters,
  type ConnectorType,
} from "@/lib/stations";
import type { UnitSystem } from "@/lib/units";
import { fieldClass } from "@/components/ui/field";

const selectClass = `${fieldClass} w-full px-2`;

/**
 * Station filters: connector (multi-select), network, charging level, minimum
 * power and radius. Controlled: the page keeps the state in the URL. With no
 * explicit connector choice the vehicle's connectors are selected (F6).
 */
export function StationFilters({
  filters,
  units,
  vehicleConnectors,
  networkOptions,
  showRadius = true,
  onChange,
}: {
  filters: ChargingFilters;
  units: UnitSystem;
  vehicleConnectors: readonly ConnectorType[];
  networkOptions: readonly string[];
  showRadius?: boolean;
  onChange: (next: ChargingFilters) => void;
}) {
  const id = useId();
  const selected = effectiveConnectors(filters, vehicleConnectors);
  const isVehicleDefault = filters.connectors === null && vehicleConnectors.length > 0;
  const radii = radiusOptions(units);
  const radiusKm = filters.radiusKm ?? defaultRadiusKm(units);
  const radiusInList = radii.some((r) => r.km === radiusKm);
  const networks = [...new Set([...filters.networks, ...networkOptions])].sort((a, b) =>
    a.localeCompare(b),
  );

  const set = (patch: Partial<ChargingFilters>) => onChange({ ...filters, ...patch });

  return (
    <form
      aria-label="Station filters"
      onSubmit={(e) => e.preventDefault()}
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5"
    >
      <fieldset className="space-y-1 sm:col-span-2 lg:col-span-5">
        <legend className="text-sm font-medium">Connectors</legend>
        <div className="flex flex-wrap items-center gap-2">
          {CONNECTOR_TYPES.map((type) => {
            const on = selected.includes(type);
            return (
              <button
                key={type}
                type="button"
                aria-pressed={on}
                onClick={() => set({ connectors: toggleConnector(selected, type) })}
                className={cn(
                  "h-8 rounded-full border px-3 text-xs font-medium",
                  on
                    ? "border-foreground bg-primary text-primary-foreground"
                    : "border-border bg-background text-foreground",
                )}
              >
                {connectorLabel(type)}
              </button>
            );
          })}
          {selected.length > 0 ? (
            <button
              type="button"
              onClick={() => set({ connectors: "any" })}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              Any connector
            </button>
          ) : null}
          {vehicleConnectors.length > 0 && !isVehicleDefault ? (
            <button
              type="button"
              onClick={() => set({ connectors: null })}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              Use my vehicle&apos;s connectors
            </button>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground">
          {isVehicleDefault
            ? "Showing your vehicle's connectors."
            : selected.length === 0
              ? "Showing every connector type."
              : null}
        </p>
      </fieldset>

      <div className="space-y-1">
        <label htmlFor={`${id}-network`} className="text-sm font-medium">
          Network
        </label>
        <select
          id={`${id}-network`}
          className={selectClass}
          value={filters.networks[0] ?? ""}
          onChange={(e) => set({ networks: e.target.value ? [e.target.value] : [] })}
        >
          <option value="">Any network</option>
          {networks.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-1">
        <label htmlFor={`${id}-level`} className="text-sm font-medium">
          Charging level
        </label>
        <select
          id={`${id}-level`}
          className={selectClass}
          value={filters.level ?? ""}
          onChange={(e) =>
            set({
              level:
                e.target.value === "level2" || e.target.value === "dc_fast" ? e.target.value : null,
            })
          }
        >
          <option value="">All levels</option>
          <option value="dc_fast">DC fast</option>
          <option value="level2">Level 2</option>
        </select>
      </div>

      <div className="space-y-1">
        <label htmlFor={`${id}-power`} className="text-sm font-medium">
          Minimum power
        </label>
        <select
          id={`${id}-power`}
          className={selectClass}
          value={filters.minPowerKw === null ? "" : String(filters.minPowerKw)}
          onChange={(e) => set({ minPowerKw: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">Any power</option>
          {[...new Set([...MIN_POWER_OPTIONS, ...(filters.minPowerKw ? [filters.minPowerKw] : [])])]
            .sort((a, b) => a - b)
            .map((kw) => (
              <option key={kw} value={kw}>
                {kw} kW or more
              </option>
            ))}
        </select>
      </div>

      {showRadius ? (
        <div className="space-y-1">
          <label htmlFor={`${id}-radius`} className="text-sm font-medium">
            Radius
          </label>
          <select
            id={`${id}-radius`}
            className={selectClass}
            value={String(radiusKm)}
            onChange={(e) => set({ radiusKm: Number(e.target.value) })}
          >
            {radiusInList ? null : (
              <option value={String(radiusKm)}>
                {Math.round(units === "imperial" ? radiusKm / 1.609344 : radiusKm)}{" "}
                {units === "imperial" ? "mi" : "km"}
              </option>
            )}
            {radii.map((r) => (
              <option key={r.km} value={String(r.km)}>
                {r.label}
              </option>
            ))}
          </select>
        </div>
      ) : null}
    </form>
  );
}
