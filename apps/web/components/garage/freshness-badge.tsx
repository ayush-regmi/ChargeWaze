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
    "bg-info/10 text-info ring-info/30",
  stale:
    "bg-warning/10 text-warning ring-warning/30",
  fresh:
    "bg-success/10 text-success ring-success/30",
} as const;

/** Last-update line for a vehicle: when data last arrived and whether it's stale. */
export function FreshnessBadge({
  vehicle,
}: {
  vehicle: Pick<VehicleSummary, "last_state_recorded_at" | "is_stale">;
}) {
  const kind = freshness(vehicle);
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
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
