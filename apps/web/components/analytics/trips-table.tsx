import Link from "next/link";

import type { UnitSystem } from "@/lib/units";

import {
  formatConsumption,
  formatDistanceValue,
  formatKwh,
  formatSignedKwh,
  MISSING,
  QUALITY_LABELS,
  qualityFlag,
  qualityReasonLabel,
  reviewHref,
  reviewLabel,
  tripTimeLabel,
  type AnalyticsTrip,
} from "./format";

const BADGE_STYLES = {
  high: "bg-success/10 text-success",
  medium: "bg-info/10 text-info",
  low: "bg-warning/10 text-warning",
} as const;

const TAG = "whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium";

/**
 * The measurement quality of one leg (phase5_plan D8). The reasons show on
 * hover and on keyboard focus, and are the badge's accessible description.
 */
export function QualityBadge({
  id,
  flag,
  reasons,
}: {
  id: string;
  flag: string;
  reasons: readonly string[];
}) {
  const q = qualityFlag(flag);
  const tipId = `quality-${id}`;
  const text = reasons.length
    ? reasons.map(qualityReasonLabel)
    : ["Meets every quality rule"];
  return (
    <span className="group relative inline-block">
      <span
        tabIndex={0}
        aria-describedby={tipId}
        data-quality={q}
        className={`${TAG} ${BADGE_STYLES[q]} cursor-help outline-offset-2`}
      >
        {QUALITY_LABELS[q]}
      </span>
      <span
        id={tipId}
        role="tooltip"
        className="invisible absolute right-0 z-10 mt-1 w-64 rounded-md border border-border bg-background p-2 text-left text-xs font-normal text-foreground shadow-md group-focus-within:visible group-hover:visible"
      >
        <ul className="list-disc space-y-0.5 pl-4">
          {text.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </span>
    </span>
  );
}

/** When the drive happened: an exact start, or a bounded one ("between X and Y"), then the end. */
function ZonedTime({ iso, tz }: { iso: string; tz: string }) {
  return <time dateTime={iso}>{tripTimeLabel(iso, tz)}</time>;
}

function DriveTime({ trip, tz }: { trip: AnalyticsTrip; tz: string }) {
  const bounded = trip.start_time !== trip.start_time_latest;
  return (
    <div className="space-y-0.5">
      <div>
        Ended <ZonedTime iso={trip.end_time} tz={tz} />
      </div>
      <div className="text-xs text-muted-foreground">
        {bounded ? (
          <>
            Started between <ZonedTime iso={trip.start_time} tz={tz} /> and{" "}
            <ZonedTime iso={trip.start_time_latest} tz={tz} />
          </>
        ) : (
          <>
            Started <ZonedTime iso={trip.start_time} tz={tz} />
          </>
        )}
      </div>
      {trip.planned_trip_id ? (
        <Link
          href={`/trips/${trip.planned_trip_id}`}
          className="text-xs link"
        >
          Plan: {trip.plan_origin_label ?? "Unnamed start"} →{" "}
          {trip.plan_destination_label ?? "Unnamed destination"}
        </Link>
      ) : null}
    </div>
  );
}

function Status({ trip }: { trip: AnalyticsTrip }) {
  return (
    <div className="flex flex-wrap gap-1">
      {trip.review_status === "needs_review" ? (
        <Link
          href={reviewHref(trip)}
          className={`${TAG} bg-warning/10 text-warning underline-offset-2 hover:underline`}
        >
          Needs review
        </Link>
      ) : (
        <span
          className={`${TAG} bg-muted text-foreground`}
        >
          {reviewLabel(trip.review_status)}
        </span>
      )}
      {trip.in_progress ? (
        <span
          className={`${TAG} bg-muted text-foreground`}
        >
          In progress
        </span>
      ) : null}
      {trip.is_simulated ? (
        <span
          className={`${TAG} bg-warning/10 text-warning`}
        >
          Simulated
        </span>
      ) : null}
      {!trip.included_in_aggregates ? (
        <span
          className={`${TAG} border border-border text-muted-foreground`}
        >
          Not in totals
        </span>
      ) : null}
    </div>
  );
}

function battery(trip: AnalyticsTrip): string {
  if (trip.soc_start_percent === null && trip.soc_end_percent === null)
    return MISSING;
  return `${trip.soc_start_percent ?? MISSING} → ${trip.soc_end_percent ?? MISSING} %`;
}

function observed(trip: AnalyticsTrip): string {
  const kwh = formatKwh(trip.observed_energy_kwh);
  if (!kwh) return MISSING;
  return trip.observed_energy_uncertainty_kwh !== null
    ? `${kwh} ± ${trip.observed_energy_uncertainty_kwh.toFixed(2)}`
    : kwh;
}

/**
 * One row per observed leg from `GET /analytics/trips` (contract §3.11a),
 * low-quality and in-progress ones included and flagged, so the table
 * explains the charts above it.
 */
export function TripsTable({
  trips,
  units,
  tz,
}: {
  trips: readonly AnalyticsTrip[];
  units: UnitSystem;
  /** The analytics time zone (the API's `tz`); every time in the table is shown in it. */
  tz: string;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border [contain:inline-size] border-border">
      <table
        className="w-full min-w-[56rem] text-left text-sm"
        data-testid="analytics-trips-table"
      >
        <caption className="sr-only">
          Observed drives, newest first. Times in {tz}.
        </caption>
        <thead className="bg-muted/50 text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              Drive{" "}
              <span className="font-normal" data-testid="trips-table-tz">
                (times in {tz})
              </span>
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Distance
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Battery
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Observed
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Estimated
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Error
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Consumption
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Quality
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Status
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {trips.map((trip) => (
            <tr key={trip.id} data-trip-id={trip.id} className="align-top">
              <td className="px-3 py-2">
                <DriveTime trip={trip} tz={tz} />
              </td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                {formatDistanceValue(trip.distance_km, units) ?? MISSING}
              </td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                {battery(trip)}
              </td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                {observed(trip)}
              </td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                {formatKwh(trip.estimated_energy_kwh) ?? MISSING}
              </td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                {formatSignedKwh(trip.error_kwh) ?? MISSING}
              </td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                {formatConsumption(trip.observed_kwh_per_km, units) ?? MISSING}
              </td>
              <td className="px-3 py-2">
                <QualityBadge
                  id={trip.id}
                  flag={trip.quality_flag}
                  reasons={trip.quality_reasons}
                />
              </td>
              <td className="px-3 py-2">
                <Status trip={trip} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
