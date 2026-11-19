import type { UnitSystem } from "@/lib/units";

import { BarChart } from "./charts/bar-chart";
import { LineChart } from "./charts/line-chart";
import { ScatterChart } from "./charts/scatter-chart";
import {
  consumptionUnit,
  distanceUnitLabel,
  groupLabel,
  qualityFlag,
  QUALITY_LABELS,
  toConsumption,
  toDistance,
  tripTimeLabel,
  type AnalyticsTrip,
  type ConsumptionBucket,
  type GroupBy,
} from "./format";

function fixed(digits: number, unit: string) {
  return (v: number) =>
    `${v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${unit}`;
}

function Panel({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        {description}
      </p>
      {children}
    </section>
  );
}

/**
 * The three Analytics charts (phase5_plan F6): distance per bucket, observed
 * vs estimated consumption per bucket, and per-trip estimated vs observed
 * energy. Values are converted to the user's units here.
 */
export function AnalyticsCharts({
  series,
  trips,
  groupBy,
  units,
  tz,
  partialTrips,
}: {
  series: readonly ConsumptionBucket[];
  trips: readonly AnalyticsTrip[];
  groupBy: GroupBy;
  units: UnitSystem;
  tz: string;
  /** True when the trips list has more pages, so the scatter shows only some trips. */
  partialTrips: boolean;
}) {
  const dist = distanceUnitLabel(units);
  const cons = consumptionUnit(units);
  const bucket = groupLabel(groupBy);
  const labels = series.map((b) => b.bucket);
  const scatter = trips.filter(
    (t) =>
      t.observed_energy_kwh !== null &&
      t.estimated_energy_kwh !== null &&
      !t.in_progress,
  );

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel
        title={`Distance per ${bucket.toLowerCase()}`}
        description="Every counted drive in the range."
      >
        <BarChart
          label={`Distance driven per ${bucket.toLowerCase()}, in ${dist}`}
          yLabel={dist}
          categoryHeader={bucket}
          valueHeader={`Distance (${dist})`}
          bars={series.map((b) => ({
            label: b.bucket,
            value: toDistance(b.distance_km, units),
          }))}
          format={fixed(1, dist)}
        />
      </Panel>
      <Panel
        title="Observed vs estimated consumption"
        description="Distance-weighted, over drives with energy data. Gaps are periods without any."
      >
        {series.every(
          (b) =>
            b.observed_kwh_per_km === null && b.estimated_kwh_per_km === null,
        ) ? (
          <p className="text-sm text-neutral-500 dark:text-neutral-400">
            No consumption to compare in this range yet.
          </p>
        ) : (
          <LineChart
            label={`Observed and estimated consumption per ${bucket.toLowerCase()}, in ${cons}`}
            yLabel={cons}
            categoryHeader={bucket}
            labels={labels}
            series={[
              {
                name: "Observed",
                variant: "primary",
                values: series.map((b) =>
                  toConsumption(b.observed_kwh_per_km, units),
                ),
              },
              {
                name: "Estimated",
                variant: "secondary",
                values: series.map((b) =>
                  toConsumption(b.estimated_kwh_per_km, units),
                ),
              },
            ]}
            format={fixed(units === "imperial" ? 0 : 1, cons)}
          />
        )}
      </Panel>
      <div className="lg:col-span-2">
        <Panel
          title="Energy per drive: estimated vs observed"
          description={
            "Dots above the dashed line used more energy than the model expected. Hollow dots are low-quality measurements." +
            (partialTrips
              ? " Only the drives in the table below are shown."
              : "")
          }
        >
          {scatter.length === 0 ? (
            <p className="text-sm text-neutral-500 dark:text-neutral-400">
              No drives with both an observed and an estimated energy yet.
            </p>
          ) : (
            <ScatterChart
              label="Estimated vs observed energy per drive, in kWh"
              xLabel="Estimated kWh"
              yLabel="Observed kWh"
              columns={["Drive ended", "Estimated", "Observed", "Quality"]}
              points={scatter.map((t) => ({
                id: t.id,
                rowLabel: tripTimeLabel(t.end_time, tz),
                x: t.estimated_energy_kwh as number,
                y: t.observed_energy_kwh as number,
                hollow: qualityFlag(t.quality_flag) === "low",
                note: QUALITY_LABELS[qualityFlag(t.quality_flag)],
              }))}
              format={fixed(2, "kWh")}
            />
          )}
        </Panel>
      </div>
    </div>
  );
}
