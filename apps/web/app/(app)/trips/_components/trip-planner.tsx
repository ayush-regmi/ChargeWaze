"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { DataAttribution } from "@/components/map/attribution";
import { LazyMapView } from "@/components/map/lazy-map-view";
import type { MapRoute, MapSegment, MapStation } from "@/components/map/types";
import type { Place } from "@/components/places/actions";
import { PlaceSearch } from "@/components/places/place-search";
import type { Preferences } from "@/components/profile/preferences";
import { UseMyLocation } from "@/components/places/use-my-location";
import { StationCard } from "@/components/stations/station-card";
import { StationFilters } from "@/components/stations/station-filters";
import { StationsEmpty, StationsError } from "@/components/stations/station-states";
import { Button } from "@/components/ui/button";
import { formatDistanceText } from "@/lib/geo-format";
import { sliceRouteByKm, stopSocLabel, type PlanOutcome } from "@/lib/plan-format";
import type { ConnectorType } from "@/lib/stations";
import {
  DEFAULT_TRIP_FILTERS,
  MAX_CORRIDOR_KM,
  EMPTY_TRIP,
  MIN_CORRIDOR_KM,
  alongRouteFiltersKey,
  corridorKm,
  endpointsKey,
  serializeTripState,
  toAlongRouteFilters,
  tripHref,
  type TripPoint,
  type TripState,
} from "@/lib/trip-url";
import type { UnitSystem } from "@/lib/units";

import {
  getDirections,
  getStationsAlongRoute,
  type AlongRouteResult,
  type DirectionsErrorCode,
  type DirectionsResult,
  type DirectionsRoute,
  type NoRouteReason,
} from "./actions";
import { PlanPanel } from "./plan-panel";
import { RouteList, routeLabel, type RouteStationsSummary } from "./route-list";

export type TripVehicle = {
  id: string;
  title: string;
  /** The vehicle's connectors that stations use; empty when unknown. */
  connectors: ConnectorType[];
};

const NO_ROUTE_TEXT: Record<NoRouteReason, string> = {
  no_route: "No drivable route connects these places.",
  no_road_near_point:
    "One of these places isn't near a road. Pick a point closer to a street.",
};

const DIRECTIONS_ERROR_TEXT: Record<DirectionsErrorCode, string> = {
  MAPBOX_UNREACHABLE: "The map provider isn't responding right now.",
  MAPBOX_NOT_CONFIGURED: "Route search isn't set up on this server yet.",
  MAPBOX_ERROR: "The map provider couldn't plan this route.",
  VALIDATION_ERROR: "These places are too close together or invalid. Pick two different places.",
  UNAUTHENTICATED: "Your session has ended. Sign in again to plan a trip.",
  UNKNOWN: "Couldn't load routes.",
};

const CORRIDOR_COMMIT_MS = 300;

/** A short, stable FNV-1a hash of a route's geometry. */
function geometryHash(polyline: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < polyline.length; i++) {
    h ^= polyline.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
}

/**
 * Cache key for one route's stations under one set of filters. It includes
 * the route's geometry: a fresh Directions response for the same endpoints
 * (after swapping back, say) may reorder the alternatives or change their
 * shape, and stations found along the old line must not be shown for the new
 * one. An unchanged geometry keeps its cache entry.
 */
function stationsKey(
  endpoints: string,
  route: Pick<DirectionsRoute, "index" | "geometry_polyline6">,
  filtersKey: string,
): string {
  return `${endpoints}#${route.index}:${geometryHash(route.geometry_polyline6)}|${filtersKey}`;
}

function summarize(result: AlongRouteResult | undefined, hasVehicle: boolean): RouteStationsSummary {
  if (!result) return { status: "loading" };
  if ("error" in result) return { status: "error" };
  const count = hasVehicle
    ? result.items.filter((s) => s.compatible === true).length
    : result.items.length;
  return { status: "ok", count, truncated: result.truncated };
}

/**
 * The interactive part of `/trips` (phase3_plan.md F7): origin and
 * destination search, routes from `POST /routes/directions`, and stations
 * along each route from `POST /stations/along-route`.
 *
 * The state lives in the URL (`lib/trip-url.ts`) and is written with
 * `history.replaceState`, so a reload or shared link shows the same plan
 * without a server round trip on every filter change. Directions are fetched
 * once per origin/destination pair. Stations are fetched for the selected
 * route first and for the other routes afterwards (D15), and cached per
 * route and filter set.
 *
 * Phase 4 (phase4_plan F6) adds the charging plan for the selected route
 * (`PlanPanel`, `POST /routes/plan`). A plan is kept per route and geometry;
 * while one is shown, the map shows its stops (labelled arrival → departure
 * charge) instead of every charger, and an infeasible plan greys the route and
 * draws its limiting segment in red.
 */
export function TripPlanner({
  initial,
  units,
  vehicle,
  preferences,
}: {
  initial: TripState;
  units: UnitSystem;
  vehicle: TripVehicle | null;
  preferences: Preferences | null;
}) {
  const preferredNetworks = preferences?.preferred_networks ?? [];
  const [trip, setTrip] = useState<TripState>(initial);
  const [selectedStationId, setSelectedStationId] = useState<string | null>(null);

  const update = (next: TripState) => {
    setTrip(next);
    window.history.replaceState(null, "", tripHref(next));
  };

  // ---- Directions: once per origin/destination pair -----------------------
  const endpoints = endpointsKey(trip);
  const [directions, setDirections] = useState<{
    key: string;
    attempt: number;
    result: DirectionsResult;
  } | null>(null);
  const [directionsAttempt, setDirectionsAttempt] = useState(0);
  const directionsResult =
    directions && directions.key === endpoints && directions.attempt === directionsAttempt
      ? directions.result
      : null;

  useEffect(() => {
    if (!endpoints || !trip.origin || !trip.destination) return;
    let active = true;
    const attempt = directionsAttempt;
    void getDirections({
      origin: { lat: trip.origin.lat, lon: trip.origin.lon },
      destination: { lat: trip.destination.lat, lon: trip.destination.lon },
    }).then((result) => {
      if (active) setDirections({ key: endpoints, attempt, result });
    });
    return () => {
      active = false;
    };
    // The key covers the endpoints' coordinates; labels don't change the route.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoints, directionsAttempt]);

  const routes = directionsResult && "routes" in directionsResult ? directionsResult.routes : null;
  const selectedIndex =
    routes && routes.some((r) => r.index === trip.routeIndex)
      ? trip.routeIndex
      : (routes?.[0]?.index ?? 0);

  // ---- Stations along each route ------------------------------------------
  const alongFilters = toAlongRouteFilters(trip, {
    vehicleId: vehicle?.id ?? null,
    vehicleConnectors: vehicle?.connectors ?? [],
  });
  const filtersKey = alongRouteFiltersKey(alongFilters);
  const [stations, setStations] = useState<Record<string, AlongRouteResult>>({});
  const requested = useRef(new Set<string>());
  const [stationsAttempt, setStationsAttempt] = useState(0);

  useEffect(() => {
    if (!routes || !endpoints || routes.length === 0) return;
    let active = true;
    const fetchFor = async (index: number) => {
      const route = routes.find((r) => r.index === index);
      if (!route) return;
      const key = stationsKey(endpoints, route, filtersKey);
      if (requested.current.has(key)) return;
      requested.current.add(key);
      const result = await getStationsAlongRoute({
        geometry: route.geometry_polyline6,
        filters: alongFilters,
      });
      // Errors may be retried, so they don't stay "requested".
      if ("error" in result) requested.current.delete(key);
      setStations((s) => ({ ...s, [key]: result }));
    };
    void (async () => {
      await fetchFor(selectedIndex);
      if (!active) return;
      await Promise.all(routes.filter((r) => r.index !== selectedIndex).map((r) => fetchFor(r.index)));
    })();
    return () => {
      active = false;
    };
    // `filtersKey` stands for `alongFilters`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routes, endpoints, filtersKey, selectedIndex, stationsAttempt]);

  const routeAt = (index: number) => routes?.find((r) => r.index === index);
  const resultFor = (index: number): AlongRouteResult | undefined => {
    const route = routeAt(index);
    return endpoints && route ? stations[stationsKey(endpoints, route, filtersKey)] : undefined;
  };
  const selectedResult = routes ? resultFor(selectedIndex) : undefined;
  const hasVehicle = vehicle !== null && vehicle.connectors.length > 0;

  const summaries: Record<number, RouteStationsSummary> = {};
  for (const r of routes ?? []) summaries[r.index] = summarize(resultFor(r.index), hasVehicle);

  const selectedItems = selectedResult && "items" in selectedResult ? selectedResult.items : null;

  // ---- Charging plans, one per route (keyed like the stations cache) ------
  const [plans, setPlans] = useState<Record<string, PlanOutcome>>({});
  const selectedRoute = routeAt(selectedIndex);
  const planKey =
    endpoints && selectedRoute ? stationsKey(endpoints, selectedRoute, "plan") : null;
  const planOutcome = planKey ? (plans[planKey] ?? null) : null;
  const shownPlan = planOutcome?.kind === "plan" ? planOutcome.plan : null;
  const networkOptions = [
    ...new Set([
      ...preferredNetworks,
      ...(selectedItems?.map((s) => s.network).filter((n): n is string => Boolean(n)) ?? []),
    ]),
  ];

  // The React Compiler memoizes these, so the map only refits when they change.
  const mapRoutes: MapRoute[] = (routes ?? []).map((r) => ({
    index: r.index,
    geometry_polyline6: r.geometry_polyline6,
  }));
  const mapStations: MapStation[] = shownPlan
    ? shownPlan.stops.map((s) => ({
        id: s.station.id,
        location: s.station.location,
        name: `Stop ${s.sequence}: ${s.station.name}`,
        compatible: true,
        kind: "stop",
        label: stopSocLabel(s),
      }))
    : (selectedItems ?? []).map((s) => ({
        id: s.id,
        location: s.location,
        name: s.name,
        compatible: s.compatible,
      }));
  const limiting = shownPlan?.reason?.limiting_segment ?? null;
  const limitingPoints =
    limiting && shownPlan?.route
      ? sliceRouteByKm(
          shownPlan.route.geometry_polyline6,
          shownPlan.route.distance_km,
          limiting.from_km,
          limiting.to_km,
        )
      : null;
  const mapSegments: MapSegment[] = limitingPoints
    ? [{ id: `limiting-${shownPlan?.trip_id}`, coordinates: limitingPoints }]
    : [];

  // ---- Corridor: a slider, committed after a short pause ------------------
  const [corridorDraft, setCorridorDraft] = useState(corridorKm(trip));
  const corridorTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (corridorTimer.current) clearTimeout(corridorTimer.current);
  }, []);
  const onCorridor = (km: number) => {
    setCorridorDraft(km);
    if (corridorTimer.current) clearTimeout(corridorTimer.current);
    corridorTimer.current = setTimeout(() => {
      setTrip((t) => {
        const next = { ...t, corridorKm: km };
        window.history.replaceState(null, "", tripHref(next));
        return next;
      });
    }, CORRIDOR_COMMIT_MS);
  };
  const corridorText = formatDistanceText(corridorKm(trip), units) ?? "";
  const filtersChanged =
    serializeTripState({ ...EMPTY_TRIP, filters: trip.filters, corridorKm: trip.corridorKm }) !==
    serializeTripState(EMPTY_TRIP);

  // ---- Endpoint edits -----------------------------------------------------
  const setEndpoint = (which: "origin" | "destination", point: TripPoint) => {
    setSelectedStationId(null);
    update({ ...trip, [which]: point, routeIndex: 0 });
  };
  const fromPlace = (p: Place): TripPoint => ({
    lat: p.location.lat,
    lon: p.location.lon,
    label: p.name,
  });
  const swap = () => {
    setSelectedStationId(null);
    update({ ...trip, origin: trip.destination, destination: trip.origin, routeIndex: 0 });
  };

  const selectStation = (id: string) => {
    setSelectedStationId(id);
    document
      .querySelector(`[data-station-id="${CSS.escape(id)}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  const pointKey = (p: TripPoint | null) => (p ? `${p.lat},${p.lon},${p.label ?? ""}` : "none");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <PlaceSearch
          key={`from-${pointKey(trip.origin)}`}
          label="From"
          near={trip.destination}
          initialValue={trip.origin?.label ?? ""}
          onSelect={(p) => setEndpoint("origin", fromPlace(p))}
          className="min-w-56 flex-1"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={swap}
          disabled={!trip.origin && !trip.destination}
          aria-label="Swap origin and destination"
        >
          ⇄ Swap
        </Button>
        <PlaceSearch
          key={`to-${pointKey(trip.destination)}`}
          label="To"
          near={trip.origin}
          initialValue={trip.destination?.label ?? ""}
          onSelect={(p) => setEndpoint("destination", fromPlace(p))}
          className="min-w-56 flex-1"
        />
      </div>
      <UseMyLocation
        label="Start from my location"
        onLocate={(p) => setEndpoint("origin", { ...p, label: "My location" })}
      />

      {vehicle === null ? (
        <p className="rounded-md bg-neutral-100 px-3 py-2 text-sm text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
          Chargers aren&apos;t checked for compatibility.{" "}
          <Link href="/garage" className="font-medium underline-offset-2 hover:underline">
            Connect a vehicle
          </Link>{" "}
          to filter by its connectors.
        </p>
      ) : vehicle.connectors.length === 0 ? (
        <p className="rounded-md bg-neutral-100 px-3 py-2 text-sm text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
          Your {vehicle.title}&apos;s connectors aren&apos;t set, so compatibility can&apos;t be
          checked.{" "}
          <Link
            href={`/garage/${vehicle.id}`}
            className="font-medium underline-offset-2 hover:underline"
          >
            Add them in the Garage
          </Link>
          .
        </p>
      ) : null}

      {!trip.origin || !trip.destination ? (
        <StationsEmpty>
          Choose where you&apos;re starting and where you&apos;re going to see routes and the
          chargers along them.
        </StationsEmpty>
      ) : (
        <>
          <div className="space-y-4">
            <StationFilters
              filters={trip.filters}
              units={units}
              vehicleConnectors={vehicle?.connectors ?? []}
              networkOptions={networkOptions}
              showRadius={false}
              onChange={(filters) => update({ ...trip, filters })}
            />
            <div className="flex flex-wrap items-center gap-3">
              <label htmlFor="trip-corridor" className="text-sm font-medium">
                Distance from route
              </label>
              <input
                id="trip-corridor"
                type="range"
                min={MIN_CORRIDOR_KM}
                max={MAX_CORRIDOR_KM}
                step={0.5}
                value={corridorDraft}
                onChange={(e) => onCorridor(Number(e.target.value))}
                aria-valuetext={formatDistanceText(corridorDraft, units) ?? undefined}
                className="w-48"
              />
              <span className="text-sm text-neutral-700 dark:text-neutral-300">
                {formatDistanceText(corridorDraft, units)}
              </span>
              {filtersChanged ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setCorridorDraft(corridorKm({ corridorKm: null }));
                    update({ ...trip, filters: DEFAULT_TRIP_FILTERS, corridorKm: null });
                  }}
                >
                  Reset filters
                </Button>
              ) : null}
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_26rem]">
            <div className="h-80 lg:sticky lg:top-4 lg:h-[40rem]">
              <LazyMapView
                label="Trip map"
                routes={mapRoutes}
                selectedRouteIndex={routes ? selectedIndex : null}
                selectedRouteTone={shownPlan?.status === "infeasible" ? "infeasible" : "default"}
                segments={mapSegments}
                onSelectRoute={(index) => update({ ...trip, routeIndex: index })}
                stations={mapStations}
                selectedStationId={selectedStationId}
                onSelect={selectStation}
                className="h-full"
              />
            </div>

            <div className="space-y-4">
              {directionsResult === null ? (
                <p role="status" className="text-sm text-neutral-500">
                  Finding routes…
                </p>
              ) : "error" in directionsResult ? (
                <div
                  role="alert"
                  className="space-y-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm dark:border-red-900 dark:bg-red-950/40"
                >
                  <p className="text-red-700 dark:text-red-300">
                    {DIRECTIONS_ERROR_TEXT[directionsResult.error]}
                  </p>
                  {directionsResult.error === "UNAUTHENTICATED" ? (
                    <Link
                      href={`/sign-in?next=${encodeURIComponent(tripHref(trip))}`}
                      className="font-medium underline-offset-2 hover:underline"
                    >
                      Sign in
                    </Link>
                  ) : (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => setDirectionsAttempt((n) => n + 1)}
                    >
                      Try again
                    </Button>
                  )}
                </div>
              ) : directionsResult.routes.length === 0 ? (
                <StationsEmpty>
                  {NO_ROUTE_TEXT[directionsResult.noRouteReason ?? "no_route"]}
                </StationsEmpty>
              ) : (
                <>
                  <RouteList
                    routes={directionsResult.routes}
                    selectedIndex={selectedIndex}
                    onSelect={(index) => {
                      setSelectedStationId(null);
                      update({ ...trip, routeIndex: index });
                    }}
                    units={units}
                    summaries={summaries}
                    hasVehicle={hasVehicle}
                    corridorText={corridorText}
                  />
                  {planKey && trip.origin && trip.destination ? (
                    <PlanPanel
                      key={planKey}
                      vehicleId={vehicle?.id ?? null}
                      vehicleTitle={vehicle?.title ?? null}
                      origin={trip.origin}
                      destination={trip.destination}
                      routeIndex={selectedIndex}
                      routeName={routeLabel(selectedIndex)}
                      preferences={preferences}
                      units={units}
                      outcome={planOutcome}
                      onOutcome={(outcome) => setPlans((p) => ({ ...p, [planKey]: outcome }))}
                      signInHref={`/sign-in?next=${encodeURIComponent(tripHref(trip))}`}
                    />
                  ) : null}
                  <section
                    aria-label="Chargers along the route"
                    aria-busy={selectedResult === undefined || undefined}
                    className="space-y-3"
                  >
                    <h2 className="text-base font-semibold">
                      Chargers along {routeLabel(selectedIndex)}
                    </h2>
                    {selectedResult === undefined ? (
                      <p role="status" className="text-sm text-neutral-500">
                        Loading chargers…
                      </p>
                    ) : "error" in selectedResult ? (
                      selectedResult.error === "UNAUTHENTICATED" ? (
                        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
                          Your session has ended. Sign in again to see chargers.
                        </p>
                      ) : (
                        <StationsError
                          code={selectedResult.error}
                          onRetry={() => {
                            const route = routeAt(selectedIndex);
                            const key =
                              endpoints && route ? stationsKey(endpoints, route, filtersKey) : null;
                            if (key) {
                              setStations((s) => {
                                const next = { ...s };
                                delete next[key];
                                return next;
                              });
                            }
                            setStationsAttempt((n) => n + 1);
                          }}
                          onReset={() => update({ ...trip, filters: DEFAULT_TRIP_FILTERS })}
                        />
                      )
                    ) : (
                      <>
                        <p
                          role="status"
                          data-testid="route-station-count"
                          className="text-sm text-neutral-600 dark:text-neutral-400"
                        >
                          {selectedResult.items.length}{" "}
                          {selectedResult.items.length === 1 ? "station" : "stations"} within{" "}
                          {corridorText} of {routeLabel(selectedIndex)}
                          {selectedResult.unknownPowerCount > 0
                            ? ` · ${selectedResult.unknownPowerCount} with unknown power`
                            : null}
                        </p>
                        {selectedResult.truncated ? (
                          <p className="text-xs text-neutral-500">
                            The provider has more stations than shown, usually near the end of
                            the route. Narrow the filters to see them.
                          </p>
                        ) : null}
                        {selectedResult.items.length === 0 ? (
                          <StationsEmpty>
                            No stations match these filters along this route. Try a wider
                            distance from the route or fewer filters.
                          </StationsEmpty>
                        ) : (
                          <ol className="space-y-2">
                            {selectedResult.items.map((station) => (
                              <li key={station.id}>
                                <StationCard
                                  station={station}
                                  href={`/charging/${station.id}`}
                                  distanceText={`${formatDistanceText(
                                    station.distance_along_route_km,
                                    units,
                                  )} along · ${formatDistanceText(
                                    station.distance_from_route_km,
                                    units,
                                  )} off route`}
                                  selected={station.id === selectedStationId}
                                  onSelect={setSelectedStationId}
                                />
                              </li>
                            ))}
                          </ol>
                        )}
                      </>
                    )}
                  </section>
                </>
              )}
              <DataAttribution />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
