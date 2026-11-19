import type { ReactNode } from "react";

import { formatMinutes, formatSoc } from "@/components/trips/plan-payload";
import { formatDistanceText } from "@/lib/geo-format";
import type { PlanResponse } from "@/lib/plan-format";
import type { UnitSystem } from "@/lib/units";

const SOURCE_LABELS: Record<string, string> = {
  vehicle: "your vehicle specs",
  catalog: "the vehicle catalog",
  catalog_derived: "derived from published figures, not measured",
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-3 py-1 text-sm">
      <dt className="text-neutral-600 dark:text-neutral-400">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/**
 * "How this was calculated": where each vehicle number came from, the
 * preferences used and which stations were left out (phase4_plan F6, §3.7
 * `assumptions` and `candidates`).
 */
export function PlanAssumptions({ plan, units }: { plan: PlanResponse; units: UnitSystem }) {
  const a = plan.assumptions;
  const c = plan.candidates;
  return (
    <details
      className="rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800"
      data-testid="plan-assumptions"
    >
      <summary className="cursor-pointer font-medium">How this was calculated</summary>
      <dl className="mt-2">
        <Row label="Usable battery">
          {a.usable_capacity_kwh} kWh ({SOURCE_LABELS[a.usable_capacity_source]})
        </Row>
        <Row label="Consumption">
          {a.baseline_kwh_per_km} kWh/km ({SOURCE_LABELS[a.baseline_source]})
        </Row>
        <Row label="Charging curve">
          {SOURCE_LABELS[a.charging_curve_source] ?? a.charging_curve_source}
        </Row>
        <Row label="Reserve">{formatSoc(a.reserve_soc)} at every arrival</Row>
        <Row label="Charge to">{formatSoc(a.charge_to_soc)}</Row>
        <Row label="Max detour">{formatMinutes(a.max_detour_minutes)}</Row>
        <Row label="Per-stop overhead">{formatMinutes(a.stop_overhead_minutes)}</Row>
        <Row label="Preferred networks">
          {a.preferred_networks.length > 0 ? a.preferred_networks.join(", ") : "None"}
          {a.preferred_networks_relaxed
            ? " (no plan used only these, so all networks were allowed)"
            : ""}
        </Row>
        <Row label="Stations">
          {c.searched ? (
            <>
              {c.considered} considered within {formatDistanceText(c.corridor_km, units)} of the
              route; excluded {c.excluded_incompatible} incompatible, {c.excluded_unknown_power}{" "}
              with unknown power, {c.excluded_detour} too far off route
            </>
          ) : plan.status === "feasible" ? (
            "Not searched: no charging needed."
          ) : (
            "Not searched: no charger could help from this starting charge."
          )}
        </Row>
      </dl>
      <p className="mt-2 text-xs text-neutral-500">
        Speed, temperature, elevation and charger availability aren&apos;t modelled. Detours are
        estimates. Unknown-power chargers are never used.
      </p>
    </details>
  );
}
