"use client";

import { useMemo, useState } from "react";

import { LazyMapView } from "@/components/map/lazy-map-view";
import type { MapRoute, MapSegment, MapStation } from "@/components/map/types";
import { sliceRouteByKm, stopSocLabel } from "@/lib/plan-format";

import type { StoredPlan } from "./plan-payload";

/**
 * The stored route and its charging stops on the map. Drawn from the stored
 * plan only: nothing is fetched or recomputed (phase4_plan F7). An infeasible
 * plan's route is greyed and its limiting segment drawn in red (added by F6).
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
                kind: "stop" as const,
                label: stopSocLabel(s),
              },
            ]
          : [],
      ),
    [plan.stops],
  );

  // An infeasible plan's limiting segment, cut from the stored route.
  const segments = useMemo<MapSegment[]>(() => {
    const seg = plan.reason?.limiting_segment;
    const route = plan.route;
    if (!seg || !route?.geometry_polyline6 || route.distance_km === null) return [];
    if (seg.from_km === null || seg.to_km === null) return [];
    const points = sliceRouteByKm(route.geometry_polyline6, route.distance_km, seg.from_km, seg.to_km);
    return points ? [{ id: "limiting-segment", coordinates: points }] : [];
  }, [plan.reason, plan.route]);

  return (
    <LazyMapView
      routes={routes}
      selectedRouteIndex={routes[0]?.index ?? null}
      selectedRouteTone={plan.status === "infeasible" ? "infeasible" : "default"}
      segments={segments}
      stations={stations}
      selectedStationId={selected}
      onSelect={setSelected}
      center={plan.origin}
      label={label}
      className="h-80"
    />
  );
}
