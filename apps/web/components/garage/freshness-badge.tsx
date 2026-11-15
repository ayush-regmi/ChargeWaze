import { cn } from "@/lib/utils";
import { freshness, type VehicleSummary } from "@/lib/vehicles";

import { LocalTime } from "./local-time";

const LABELS = {
  awaiting: "Awaiting first update",
  stale: "Stale",
  fresh: "Up to date",
} as const;

const TONES = {
  awaiting:
    "bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-800",
  stale:
    "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-800",
  fresh:
    "bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-800",
} as const;

/** Last-update line for a vehicle: when data last arrived and whether it's stale. */
export function FreshnessBadge({
  vehicle,
}: {
  vehicle: Pick<VehicleSummary, "last_state_recorded_at" | "is_stale">;
}) {
  const kind = freshness(vehicle);
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-neutral-600 dark:text-neutral-400">
      <span
        data-freshness={kind}
        className={cn("rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", TONES[kind])}
      >
        {LABELS[kind]}
      </span>
      {vehicle.last_state_recorded_at ? (
        <span>
          Last update <LocalTime iso={vehicle.last_state_recorded_at} />
        </span>
      ) : null}
    </div>
  );
}
