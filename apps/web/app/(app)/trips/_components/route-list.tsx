"use client";

import { formatDistanceText } from "@/lib/geo-format";
import type { UnitSystem } from "@/lib/units";
import { cn } from "@/lib/utils";

import type { DirectionsRoute } from "./actions";

/** "Route A" for index 0, "Route B" for 1, … */
export function routeLabel(index: number): string {
  return `Route ${String.fromCharCode(65 + index)}`;
}

/** "8 h 35 min", "45 min". */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const totalMin = Math.round(seconds / 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** The `index` of the shortest route by distance, or null with fewer than two routes. */
export function shortestRouteIndex(routes: readonly Pick<DirectionsRoute, "index" | "distance_m">[]): number | null {
  if (routes.length < 2) return null;
  return routes.reduce((best, r) => (r.distance_m < best.distance_m ? r : best)).index;
}

/** Per-route station summary shown in the route row. */
export type RouteStationsSummary =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ok"; count: number; truncated: boolean };

/**
 * "12 compatible chargers within 1.9 mi". With a vehicle only compatible
 * stations are counted; without one there is nothing to check against.
 */
export function chargersText(
  summary: RouteStationsSummary | undefined,
  options: { hasVehicle: boolean; corridorText: string },
): string {
  if (!summary || summary.status === "loading") return "Finding chargers…";
  if (summary.status === "error") return "Chargers unavailable";
  const noun = summary.count === 1 ? "charger" : "chargers";
  const kind = options.hasVehicle ? `compatible ${noun}` : noun;
  return `${summary.count}${summary.truncated ? "+" : ""} ${kind} within ${options.corridorText}`;
}

/**
 * The routes from `POST /routes/directions`, best first, as a single-select
 * list. Shows distance and duration in the user's units and marks the
 * shortest. No feasibility or arrival battery: that is Phase 4.
 */
export function RouteList({
  routes,
  selectedIndex,
  onSelect,
  units,
  summaries,
  hasVehicle,
  corridorText,
}: {
  routes: readonly DirectionsRoute[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  units: UnitSystem;
  summaries: Readonly<Record<number, RouteStationsSummary>>;
  hasVehicle: boolean;
  corridorText: string;
}) {
  const shortest = shortestRouteIndex(routes);
  return (
    <ul aria-label="Routes" className="space-y-2">
      {routes.map((route) => {
        const selected = route.index === selectedIndex;
        const crossings = route.countries.length > 1 ? route.countries.join(" → ") : null;
        return (
          <li key={route.index}>
            <button
              type="button"
              aria-pressed={selected}
              data-route-index={route.index}
              onClick={() => onSelect(route.index)}
              className={cn(
                "w-full space-y-1 rounded-lg border bg-background p-3 text-left",
                selected
                  ? "border-foreground ring-2 ring-foreground/10"
                  : "border-border hover:border-foreground/20",
              )}
            >
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{routeLabel(route.index)}</span>
                {route.index === shortest ? (
                  <span className="rounded-md bg-success/10 px-1.5 py-0.5 text-xs font-medium text-success">
                    Shortest
                  </span>
                ) : null}
              </span>
              <span className="block text-sm text-foreground">
                {formatDistanceText(route.distance_m / 1000, units)} ·{" "}
                {formatDuration(route.duration_s)}
              </span>
              {route.summary || crossings ? (
                <span className="block text-xs text-muted-foreground">
                  {route.summary ? `via ${route.summary}` : null}
                  {route.summary && crossings ? " · " : null}
                  {crossings}
                </span>
              ) : null}
              <span
                className="block text-xs font-medium text-foreground"
                data-testid="route-chargers"
              >
                {chargersText(summaries[route.index], { hasVehicle, corridorText })}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
