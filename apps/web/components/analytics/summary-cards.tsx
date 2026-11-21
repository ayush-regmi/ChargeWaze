import type { components } from "@chargewaze/api-client";

import type { UnitSystem } from "@/lib/units";

import {
  formatConsumption,
  formatDistanceValue,
  formatKwh,
  formatSignedKwh,
  MISSING,
  type ConsumptionMetrics,
} from "./format";

type PlanAccuracy = components["schemas"]["PlanAccuracy"];

function Card({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="rounded-lg border border-border p-4">
      <dt className="text-xs font-medium text-muted-foreground">
        {label}
      </dt>
      <dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd>
      {detail ? (
        <dd className="text-xs text-muted-foreground">
          {detail}
        </dd>
      ) : null}
    </div>
  );
}

/** Totals for the range: what was driven, observed vs estimated consumption, and model error. */
export function SummaryCards({
  totals,
  plans,
  units,
}: {
  totals: ConsumptionMetrics;
  plans: PlanAccuracy;
  units: UnitSystem;
}) {
  const trips = totals.trips === 1 ? "1 trip" : `${totals.trips} trips`;
  const energyTrips =
    totals.energy_trips === totals.trips
      ? "All trips have energy data"
      : `${totals.energy_trips} of ${totals.trips} with energy data`;
  const bias = formatSignedKwh(totals.bias_kwh);
  return (
    <dl
      className="grid grid-cols-2 gap-3 lg:grid-cols-4"
      data-testid="analytics-summary"
    >
      <Card
        label="Driven"
        value={formatDistanceValue(totals.distance_km, units) ?? MISSING}
        detail={trips}
      />
      <Card
        label="Observed consumption"
        value={formatConsumption(totals.observed_kwh_per_km, units) ?? MISSING}
        detail={energyTrips}
      />
      <Card
        label="Estimated consumption"
        value={formatConsumption(totals.estimated_kwh_per_km, units) ?? MISSING}
        detail="Deterministic model, same trips"
      />
      <Card
        label="Mean error per trip"
        value={formatKwh(totals.mae_kwh) ?? MISSING}
        detail={
          bias
            ? `Bias ${bias}${(totals.bias_kwh ?? 0) < 0 ? " (model over-estimates)" : (totals.bias_kwh ?? 0) > 0 ? " (model under-estimates)" : ""}`
            : undefined
        }
      />
      {plans.matched > 0 ? (
        <Card
          label="Plans driven"
          value={plans.matched.toString()}
          detail={
            [
              plans.mae_kwh !== null
                ? `Energy error ${formatKwh(plans.mae_kwh)}`
                : null,
              plans.mean_arrival_soc_error_points !== null
                ? `Arrival battery ${plans.mean_arrival_soc_error_points > 0 ? "+" : ""}${plans.mean_arrival_soc_error_points.toFixed(1)} points`
                : null,
            ]
              .filter(Boolean)
              .join(" · ") || undefined
          }
        />
      ) : null}
    </dl>
  );
}
