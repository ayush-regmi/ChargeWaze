"use client";

import { useMemo, useState } from "react";

import { LazyMapView } from "@/components/map/lazy-map-view";
import type { MapRoute, MapStation } from "@/components/map/types";

import type { StoredPlan } from "./plan-payload";

/**
 * The stored route and its charging stops on the map. Drawn from the stored
 * plan only: nothing is fetched or recomputed (phase4_plan F7).
 */
export function TripMap({ plan, label }: { plan: StoredPlan; label: string }) {
  const [selected, setSelected] = useState<string | null>(null);

  const routes = useMemo<MapRoute[]>(
    () =>
      plan.route?.geometry_polyline6
        ? [{ index: plan.route.route_index, geometry_polyline6: plan.route.geometry_polyline6 }]
        : [],
    [plan.route],
  );

  const stations = useMemo<MapStation[]>(
    () =>
      plan.stops.flatMap((s) =>
        s.station.location
          ? [
              {
                id: s.station.id ?? `stop-${s.sequence}`,
                location: s.station.location,
                name: `Stop ${s.sequence}: ${s.station.name ?? "Unnamed station"}`,
                compatible: true,
              },
            ]
          : [],
      ),
    [plan.stops],
  );

  return (
    <LazyMapView
      routes={routes}
      selectedRouteIndex={routes[0]?.index ?? null}
      stations={stations}
      selectedStationId={selected}
      onSelect={setSelected}
      center={plan.origin}
      label={label}
      className="h-80"
    />
  );
}
