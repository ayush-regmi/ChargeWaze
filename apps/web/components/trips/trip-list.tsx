import Link from "next/link";

import type { components } from "@chargewise/api-client";

import { LocalTime } from "@/components/garage/local-time";
import { formatDistanceText } from "@/lib/geo-format";
import type { UnitSystem } from "@/lib/units";

import { formatMinutes } from "./plan-payload";

export type TripListItem = components["schemas"]["TripListItem"];

const MISSING = "—";

export function PlanStatusBadge({ planStatus }: { planStatus: TripListItem["plan_status"] }) {
  return planStatus === "feasible" ? (
    <span className="rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
      Feasible
    </span>
  ) : (
    <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-xs font-medium text-destructive">
      Infeasible
    </span>
  );
}

export function tripTitle(origin: string | null, destination: string | null): string {
  return `${origin ?? "Unnamed start"} → ${destination ?? "Unnamed destination"}`;
}

function stopsText(count: number): string {
  if (count === 0) return "No charging stops";
  return count === 1 ? "1 stop" : `${count} stops`;
}

/**
 * Saved trips (phase4_plan F7). Each row shows the stored list fields only;
 * distance and total time read "—" when the plan has none (§3.8).
 */
export function TripList({
  items,
  units,
  nextHref,
  firstHref,
}: {
  items: readonly TripListItem[];
  units: UnitSystem;
  /** Link to the next page, or null on the last page. */
  nextHref: string | null;
  /** Link back to the first page, or null when already on it. */
  firstHref: string | null;
}) {
  if (items.length === 0 && !firstHref) {
    return (
      <div className="rounded-lg border border-dashed border-border p-8 text-center">
        <p className="font-medium">No saved trips yet</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Plan a trip and save it to keep it here.
        </p>
        <Link
          href="/trips"
          className="mt-4 inline-block text-sm font-medium link"
        >
          Plan a trip
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No more saved trips.</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {items.map((trip) => (
            <li key={trip.id}>
              <Link
                href={`/trips/${trip.id}`}
                className="block space-y-1 p-4 hover:bg-accent"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {tripTitle(trip.origin_label, trip.destination_label)}
                  </span>
                  <PlanStatusBadge planStatus={trip.plan_status} />
                  {trip.status === "completed" ? (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
                      Completed
                    </span>
                  ) : null}
                </div>
                <p className="text-sm text-muted-foreground">
                  {formatDistanceText(trip.distance_km, units) ?? MISSING}
                  {" · "}
                  {formatMinutes(trip.total_minutes) ?? MISSING}
                  {" · "}
                  {stopsText(trip.stop_count)}
                </p>
                <p className="text-xs text-muted-foreground">
                  Saved <LocalTime iso={trip.saved_at ?? trip.created_at} />
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {nextHref || firstHref ? (
        <nav aria-label="Saved trips pages" className="flex gap-4 text-sm">
          {firstHref ? (
            <Link href={firstHref} className="link">
              ← First page
            </Link>
          ) : null}
          {nextHref ? (
            <Link href={nextHref} className="link">
              Older trips →
            </Link>
          ) : null}
        </nav>
      ) : null}
    </div>
  );
}
