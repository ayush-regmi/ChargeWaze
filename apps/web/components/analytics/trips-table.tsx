import Link from "next/link";

import { LocalTime } from "@/components/garage/local-time";
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
  type AnalyticsTrip,
} from "./format";

const BADGE_STYLES = {
  high: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  medium: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  low: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300",
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
        className="invisible absolute right-0 z-10 mt-1 w-64 rounded-md border border-neutral-200 bg-white p-2 text-left text-xs font-normal text-neutral-700 shadow-md group-focus-within:visible group-hover:visible dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300"
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
function DriveTime({ trip }: { trip: AnalyticsTrip }) {
  const bounded = trip.start_time !== trip.start_time_latest;
  return (
    <div className="space-y-0.5">
      <div>
        Ended <LocalTime iso={trip.end_time} />
      </div>
      <div className="text-xs text-neutral-500 dark:text-neutral-400">
        {bounded ? (
          <>
            Started between <LocalTime iso={trip.start_time} /> and{" "}
            <LocalTime iso={trip.start_time_latest} />
          </>
        ) : (
          <>
            Started <LocalTime iso={trip.start_time} />
          </>
        )}
      </div>
      {trip.planned_trip_id ? (
        <Link
          href={`/trips/${trip.planned_trip_id}`}
          className="text-xs text-blue-700 hover:underline dark:text-blue-400"
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
          className={`${TAG} bg-amber-100 text-amber-900 underline-offset-2 hover:underline dark:bg-amber-950 dark:text-amber-300`}
        >
          Needs review
        </Link>
      ) : (
        <span
          className={`${TAG} bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300`}
        >
          {reviewLabel(trip.review_status)}
        </span>
      )}
      {trip.in_progress ? (
        <span
          className={`${TAG} bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300`}
        >
          In progress
        </span>
      ) : null}
      {trip.is_simulated ? (
        <span
          className={`${TAG} bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300`}
        >
          Simulated
        </span>
      ) : null}
      {!trip.included_in_aggregates ? (
        <span
          className={`${TAG} border border-neutral-300 text-neutral-600 dark:border-neutral-700 dark:text-neutral-400`}
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
}: {
  trips: readonly AnalyticsTrip[];
  units: UnitSystem;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border [contain:inline-size] border-neutral-200 dark:border-neutral-800">
      <table
        className="w-full min-w-[56rem] text-left text-sm"
        data-testid="analytics-trips-table"
      >
        <caption className="sr-only">Observed drives, newest first</caption>
        <thead className="bg-neutral-50 text-xs text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              Drive
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
        <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
          {trips.map((trip) => (
            <tr key={trip.id} data-trip-id={trip.id} className="align-top">
              <td className="px-3 py-2">
                <DriveTime trip={trip} />
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
