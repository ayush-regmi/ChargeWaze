import Link from "next/link";
import type { ReactNode } from "react";

import { formatDistanceText } from "@/lib/geo-format";
import type { UnitSystem } from "@/lib/units";

import {
  formatKwh,
  formatPercent,
  formatPoints,
  formatSignedKwh,
  pairLegs,
  type ObservationLeg,
  type TripObservation,
} from "./observation-format";
import { QualityBadge, ReviewStatusBadge } from "./observation-quality-badge";
import { DriveEnd } from "./observation-time";
import { formatSoc, type StoredPlan } from "./plan-payload";

const MISSING = "—";

function Compare({
  label,
  planned,
  observed,
  diff,
  testId,
}: {
  label: string;
  planned: ReactNode;
  observed: ReactNode;
  diff: ReactNode;
  testId: string;
}) {
  return (
    <tr data-testid={testId} className="border-t border-border">
      <th scope="row" className="py-1.5 pr-3 text-left font-normal text-muted-foreground">
        {label}
      </th>
      <td className="py-1.5 pr-3">{planned}</td>
      <td className="py-1.5 pr-3">{observed}</td>
      <td className="py-1.5">{diff}</td>
    </tr>
  );
}

function signedDistance(km: number | null, units: UnitSystem): string | null {
  if (km === null) return null;
  const text = formatDistanceText(Math.abs(km), units);
  if (!text) return null;
  if (Math.abs(km) < 0.05) return text;
  return `${km > 0 ? "+" : "−"}${text}`;
}

function LegMeta({ leg }: { leg: ObservationLeg }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {leg.quality_flag ? <QualityBadge flag={leg.quality_flag} /> : null}
      {leg.review_status ? <ReviewStatusBadge status={leg.review_status} /> : null}
    </span>
  );
}

/**
 * "Planned vs actual" on trip detail (phase5_plan F7, api-contracts §3.18e):
 * the plan's predictions next to the drives matched to it. Shown only when
 * the plan has an observation. Missing measurements read as missing, never
 * as estimates.
 */
export function ObservationPanel({
  observation,
  plan,
  units,
}: {
  observation: TripObservation;
  plan: StoredPlan | null;
  units: UnitSystem;
}) {
  const pairs = pairLegs(plan, observation);
  const distanceDiff =
    observation.observed_distance_km !== null && observation.planned_distance_km !== null
      ? observation.observed_distance_km - observation.planned_distance_km
      : null;
  const energyMissing = observation.observed_energy_kwh === null;
  const needsReview = observation.legs.some((l) => l.review_status === "needs_review");

  return (
    <section
      aria-labelledby="observation-title"
      data-testid="observation-panel"
      className="space-y-4 rounded-lg border border-border p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="observation-title" className="text-sm font-medium">
          Planned vs actual
        </h2>
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
          Completed
        </span>
        <span className="text-xs text-muted-foreground">
          {observation.legs.length === 1 ? "1 drive" : `${observation.legs.length} drives`} matched
        </span>
      </div>

      <table className="w-full text-sm" data-testid="observation-totals">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th scope="col" className="pb-1 font-normal">
              <span className="sr-only">Measure</span>
            </th>
            <th scope="col" className="pb-1 font-normal">Planned</th>
            <th scope="col" className="pb-1 font-normal">Actual</th>
            <th scope="col" className="pb-1 font-normal">Difference</th>
          </tr>
        </thead>
        <tbody>
          <Compare
            testId="obs-distance"
            label="Distance"
            planned={formatDistanceText(observation.planned_distance_km, units) ?? MISSING}
            observed={formatDistanceText(observation.observed_distance_km, units) ?? MISSING}
            diff={signedDistance(distanceDiff, units) ?? MISSING}
          />
          <Compare
            testId="obs-energy"
            label="Energy"
            planned={formatKwh(observation.predicted_energy_kwh) ?? MISSING}
            observed={energyMissing ? "Not measured" : formatKwh(observation.observed_energy_kwh)}
            diff={formatSignedKwh(observation.error_kwh) ?? MISSING}
          />
          <Compare
            testId="obs-start-soc"
            label="Starting charge"
            planned={formatSoc(plan?.starting_soc) ?? MISSING}
            observed={formatSoc(observation.observed_start_soc) ?? "Not measured"}
            diff=""
          />
          <Compare
            testId="obs-arrival-soc"
            label="Arrival charge"
            planned={formatSoc(observation.predicted_arrival_soc) ?? MISSING}
            observed={formatSoc(observation.observed_arrival_soc) ?? "Not measured"}
            diff={formatPoints(observation.arrival_soc_error_points) ?? MISSING}
          />
        </tbody>
      </table>
      {energyMissing ? (
        <p className="text-xs text-muted-foreground" data-testid="energy-missing-note">
          At least one drive has no usable battery reading, so the energy actually used isn&apos;t
          known and no error is shown.
        </p>
      ) : null}

      <div className="space-y-2">
        <h3 className="text-xs font-medium text-muted-foreground">By leg</h3>
        {pairs ? (
          <ol className="divide-y divide-border text-sm" data-testid="observation-legs">
            {pairs.map(({ planned, observed, arrival_soc_error_points }) => (
              <li key={observed.id} className="space-y-1 py-2" data-testid="observation-leg">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">Leg {planned.index}</span>
                  <LegMeta leg={observed} />
                </div>
                <p>
                  Distance {formatDistanceText(planned.distance_km, units) ?? MISSING} planned ·{" "}
                  {formatDistanceText(observed.distance_km, units) ?? MISSING} driven
                </p>
                <p data-testid="leg-soc">
                  Charge {formatSoc(planned.start_soc) ?? MISSING} →{" "}
                  {formatSoc(planned.arrival_soc) ?? MISSING} planned ·{" "}
                  {formatPercent(observed.soc_start_percent) ?? MISSING} →{" "}
                  {formatPercent(observed.soc_end_percent) ?? MISSING} actual
                  {formatPoints(arrival_soc_error_points)
                    ? ` (arrival ${formatPoints(arrival_soc_error_points)})`
                    : ""}
                </p>
                <p className="text-xs text-muted-foreground">
                  {formatKwh(observed.energy_kwh) ?? "Energy not measured"} ·{" "}
                  <DriveEnd endTime={observed.end_time} />
                </p>
              </li>
            ))}
          </ol>
        ) : (
          <>
            <p className="text-xs text-muted-foreground" data-testid="legs-unpaired">
              The drives don&apos;t line up one-to-one with the planned legs, so they&apos;re listed
              on their own.
            </p>
            <ol className="divide-y divide-border text-sm" data-testid="observation-legs">
              {observation.legs.map((leg, i) => (
                <li key={leg.id} className="space-y-1 py-2" data-testid="observation-leg">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">Drive {i + 1}</span>
                    <LegMeta leg={leg} />
                  </div>
                  <p>
                    {formatDistanceText(leg.distance_km, units) ?? MISSING} · charge{" "}
                    {formatPercent(leg.soc_start_percent) ?? MISSING} →{" "}
                    {formatPercent(leg.soc_end_percent) ?? MISSING} ·{" "}
                    {formatKwh(leg.energy_kwh) ?? "energy not measured"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    <DriveEnd endTime={leg.end_time} />
                  </p>
                </li>
              ))}
            </ol>
          </>
        )}
        <p className="text-xs text-muted-foreground">
          Planned leg distances are measured along the route and don&apos;t include detours to
          chargers.
          {observation.model_version ? ` Predictions: ${observation.model_version}.` : ""}
        </p>
      </div>

      {needsReview ? (
        <p className="text-sm">
          <Link
            href="/trips/saved?view=observed&review=needs_review"
            className="link"
          >
            Some of these drives need review →
          </Link>
        </p>
      ) : null}
    </section>
  );
}
