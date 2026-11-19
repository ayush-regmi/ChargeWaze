import Link from "next/link";

import { formatDistanceText } from "@/lib/geo-format";
import type { UnitSystem } from "@/lib/units";

import {
  formatClock,
  formatDay,
  formatKwh,
  formatPercent,
  type ObservedTrip,
} from "./observation-format";
import { QualityBadge, QualityReasons, ReviewStatusBadge } from "./observation-quality-badge";
import { ObservationReviewButtons } from "./observation-review-buttons";
import { DriveTimes } from "./observation-time";

export type ObservationFilter = "all" | "needs_review";

const MISSING = "—";

function energyText(trip: ObservedTrip): string {
  const kwh = formatKwh(trip.energy_kwh);
  if (!kwh) return "Energy not measured";
  const pm =
    trip.energy_kwh_uncertainty !== null ? ` ± ${trip.energy_kwh_uncertainty.toFixed(1)}` : "";
  return `${kwh.replace(" kWh", "")}${pm} kWh used`;
}

function socText(trip: ObservedTrip): string {
  return `${formatPercent(trip.soc_start_percent) ?? MISSING} → ${formatPercent(trip.soc_end_percent) ?? MISSING}`;
}

function DriveItem({ trip, units }: { trip: ObservedTrip; units: UnitSystem }) {
  const distance = formatDistanceText(trip.distance_km, units) ?? MISSING;
  const label = `${distance} drive ending ${formatDay(trip.end_time, "UTC")} ${formatClock(trip.end_time, "UTC")} UTC`;
  return (
    <li
      className="space-y-2 p-4"
      data-testid="observed-drive"
      data-id={trip.id}
      aria-label={label}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{distance}</span>
        <QualityBadge flag={trip.quality_flag} />
        <ReviewStatusBadge status={trip.review_status} />
        {trip.in_progress ? (
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
            In progress
          </span>
        ) : null}
        {trip.is_simulated ? (
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
            Simulated
          </span>
        ) : null}
      </div>
      <p className="text-sm text-muted-foreground">
        <DriveTimes leg={trip} />
      </p>
      <p className="text-sm">
        Battery <span data-testid="drive-soc">{socText(trip)}</span>
        {" · "}
        <span data-testid="drive-energy">{energyText(trip)}</span>
      </p>
      <QualityReasons reasons={trip.quality_reasons} />
      {trip.planned_trip_id ? (
        <p className="text-sm">
          <Link
            href={`/trips/${trip.planned_trip_id}`}
            className="link"
          >
            Matched to a planned trip
            {trip.plan_leg_index !== null ? ` (leg ${trip.plan_leg_index})` : ""} →
          </Link>
        </p>
      ) : null}
      {trip.in_progress ? (
        <p className="text-xs text-muted-foreground">
          Still being recorded. You can review it once the drive has ended.
        </p>
      ) : (
        <ObservationReviewButtons
          key={`${trip.id}:${trip.review_status}`}
          id={trip.id}
          status={trip.review_status}
          label={label}
        />
      )}
    </li>
  );
}

/**
 * Observed drives (phase5_plan F7, api-contracts §3.18): one row per driving
 * leg inferred from telemetry, with its measurement quality and the reasons,
 * and Confirm / Reject. Rejected drives are hidden by the API's default.
 */
export function ObservationList({
  items,
  units,
  filter,
  filterHrefs,
  nextHref,
  firstHref,
}: {
  items: readonly ObservedTrip[];
  units: UnitSystem;
  filter: ObservationFilter;
  filterHrefs: Record<ObservationFilter, string>;
  nextHref: string | null;
  firstHref: string | null;
}) {
  const filters: { key: ObservationFilter; label: string }[] = [
    { key: "all", label: "All drives" },
    { key: "needs_review", label: "Needs review" },
  ];
  return (
    <div className="space-y-4">
      <nav aria-label="Filter observed drives" className="flex gap-2 text-sm">
        {filters.map((f) => (
          <Link
            key={f.key}
            href={filterHrefs[f.key]}
            aria-current={filter === f.key ? "page" : undefined}
            className={
              filter === f.key
                ? "rounded-full bg-primary px-3 py-1 font-medium text-primary-foreground"
                : "rounded-full border border-border px-3 py-1 hover:bg-accent"
            }
          >
            {f.label}
          </Link>
        ))}
      </nav>

      {items.length === 0 ? (
        firstHref ? (
          <p className="text-sm text-muted-foreground">No more drives.</p>
        ) : (
          <div className="rounded-lg border border-dashed border-border p-8 text-center">
            {filter === "needs_review" ? (
              <>
                <p className="font-medium">Nothing to review</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Drives whose measurements are uncertain show up here for you to confirm.
                </p>
              </>
            ) : (
              <>
                <p className="font-medium">No drives yet</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Drives appear here automatically after you drive a connected car.
                </p>
              </>
            )}
          </div>
        )
      ) : (
        <ul
          aria-label="Observed drives"
          className="divide-y divide-border rounded-lg border border-border"
        >
          {items.map((trip) => (
            <DriveItem key={trip.id} trip={trip} units={units} />
          ))}
        </ul>
      )}

      {nextHref || firstHref ? (
        <nav aria-label="Observed drives pages" className="flex gap-4 text-sm">
          {firstHref ? (
            <Link href={firstHref} className="link">
              ← First page
            </Link>
          ) : null}
          {nextHref ? (
            <Link href={nextHref} className="link">
              Older drives →
            </Link>
          ) : null}
        </nav>
      ) : null}
    </div>
  );
}
