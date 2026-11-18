import { describe, expect, it } from "vitest";

import {
  DEFAULT_TRIP_FILTERS,
  EMPTY_TRIP,
  alongRouteFiltersKey,
  endpointsKey,
  parseTripState,
  serializeTripState,
  toAlongRouteFilters,
  tripHref,
  type TripState,
} from "@/lib/trip-url";

const FREDERICK = { lat: 39.38465459, lon: -77.40551751, label: "Tesla Supercharger" };
const TORONTO = { lat: 43.647938, lon: -79.38355, label: "Toronto" };

describe("trip URL state", () => {
  it("an empty query is an empty trip with DC fast stations and the default corridor", () => {
    expect(parseTripState(new URLSearchParams())).toEqual(EMPTY_TRIP);
    expect(serializeTripState(EMPTY_TRIP)).toBe("");
    expect(tripHref(EMPTY_TRIP)).toBe("/trips");
  });

  it("round-trips endpoints (rounded to 4 decimals), route, filters and corridor", () => {
    const state: TripState = {
      origin: FREDERICK,
      destination: TORONTO,
      routeIndex: 1,
      filters: { ...DEFAULT_TRIP_FILTERS, connectors: ["CCS1", "NACS"], networks: ["Tesla"], minPowerKw: 150 },
      corridorKm: 5,
    };
    const qs = serializeTripState(state);
    expect(qs).toBe(
      "from_lat=39.3847&from_lon=-77.4055&from=Tesla+Supercharger" +
        "&to_lat=43.6479&to_lon=-79.3835&to=Toronto&route=1" +
        "&connector=CCS1&connector=NACS&network=Tesla&min_kw=150&corridor=5",
    );
    expect(parseTripState(new URLSearchParams(qs))).toEqual({
      ...state,
      origin: { ...FREDERICK, lat: 39.3847, lon: -77.4055 },
      destination: { ...TORONTO, lat: 43.6479, lon: -79.3835 },
    });
  });

  it("keeps the level choice: DC fast is the default, 'all' and Level 2 are explicit", () => {
    const all = { ...EMPTY_TRIP, filters: { ...DEFAULT_TRIP_FILTERS, level: null } };
    expect(serializeTripState(all)).toBe("level=all");
    expect(parseTripState(new URLSearchParams("level=all")).filters.level).toBeNull();
    expect(parseTripState(new URLSearchParams("level=level2")).filters.level).toBe("level2");
    expect(parseTripState(new URLSearchParams("level=bogus")).filters.level).toBe("dc_fast");
  });

  it("accepts a Next.js searchParams object", () => {
    const s = parseTripState({ from_lat: "39.4", from_lon: "-77.4", connector: ["CCS1", "NACS"] });
    expect(s.origin).toEqual({ lat: 39.4, lon: -77.4, label: null });
    expect(s.filters.connectors).toEqual(["CCS1", "NACS"]);
  });

  it("drops invalid values instead of guessing", () => {
    const s = parseTripState(
      new URLSearchParams(
        "from_lat=91&from_lon=-77&to_lat=43&to=Toronto&route=7&corridor=40&min_kw=-1&radius=10&lat=1&lon=2",
      ),
    );
    expect(s.origin).toBeNull();
    expect(s.destination).toBeNull(); // no to_lon
    expect(s.routeIndex).toBe(0);
    expect(s.corridorKm).toBeNull();
    expect(s.filters.minPowerKw).toBeNull();
    // Charging-page params don't leak into the trip.
    expect(s.filters.location).toBeNull();
    expect(s.filters.radiusKm).toBeNull();
    expect(parseTripState(new URLSearchParams("route=1.5")).routeIndex).toBe(0);
  });

  it("never stores a session token", () => {
    const qs = serializeTripState(
      parseTripState(new URLSearchParams("from_lat=1&from_lon=2&session_token=abc")),
    );
    expect(qs).not.toContain("session_token");
  });

  it("endpointsKey ignores labels and needs both endpoints", () => {
    expect(endpointsKey({ origin: FREDERICK, destination: null })).toBeNull();
    expect(endpointsKey({ origin: FREDERICK, destination: TORONTO })).toBe(
      endpointsKey({ origin: { ...FREDERICK, label: "x" }, destination: { ...TORONTO, label: null } }),
    );
  });
});

describe("toAlongRouteFilters", () => {
  const vehicle = { vehicleId: "veh-1", vehicleConnectors: ["NACS" as const] };

  it("defaults to the vehicle's connectors, DC fast and a 3 km corridor", () => {
    expect(toAlongRouteFilters(EMPTY_TRIP, vehicle)).toEqual({
      corridor_km: 3,
      connector: ["NACS"],
      charging_level: "dc_fast",
      vehicle_id: "veh-1",
      limit: 200,
    });
  });

  it("'any connector' with a vehicle sends every type; without one sends none", () => {
    const any = { ...EMPTY_TRIP, filters: { ...DEFAULT_TRIP_FILTERS, connectors: "any" as const } };
    expect(toAlongRouteFilters(any, vehicle).connector).toEqual(["CCS1", "NACS", "CHAdeMO", "J1772"]);
    const none = toAlongRouteFilters(any, { vehicleId: null, vehicleConnectors: [] });
    expect(none.connector).toBeUndefined();
    expect(none.vehicle_id).toBeUndefined();
  });

  it("maps 'all levels', network, power and corridor", () => {
    const f = toAlongRouteFilters(
      {
        filters: { ...DEFAULT_TRIP_FILTERS, level: null, networks: ["Tesla"], minPowerKw: 150 },
        corridorKm: 8,
      },
      vehicle,
    );
    expect(f).toMatchObject({
      charging_level: "all",
      network: ["Tesla"],
      min_power_kw: 150,
      corridor_km: 8,
    });
  });

  it("the cache key changes with any filter", () => {
    const a = toAlongRouteFilters(EMPTY_TRIP, vehicle);
    const b = toAlongRouteFilters({ ...EMPTY_TRIP, corridorKm: 4 }, vehicle);
    expect(alongRouteFiltersKey(a)).toBe(alongRouteFiltersKey({ ...a }));
    expect(alongRouteFiltersKey(a)).not.toBe(alongRouteFiltersKey(b));
  });
});
