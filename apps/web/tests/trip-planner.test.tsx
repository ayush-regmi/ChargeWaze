import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AlongRouteResult,
  DirectionsResult,
  DirectionsRoute,
  RouteStation,
} from "@/app/(app)/trips/_components/actions";
import { TripPlanner } from "@/app/(app)/trips/_components/trip-planner";
import type { MapViewProps } from "@/components/map/types";
import { EMPTY_TRIP, parseTripState, type TripState } from "@/lib/trip-url";

import { makeStation } from "./station-fixture";

const actions = vi.hoisted(() => ({
  getDirections: vi.fn(),
  getStationsAlongRoute: vi.fn(),
}));
vi.mock("@/app/(app)/trips/_components/actions", () => actions);
vi.mock("@/components/places/actions", () => ({
  suggestPlaces: vi.fn(),
  retrievePlace: vi.fn(),
}));

const mapProps = vi.fn();
vi.mock("@/components/map/lazy-map-view", () => ({
  LazyMapView: (props: MapViewProps) => {
    mapProps(props);
    return <div data-testid="map-stub" />;
  },
}));

const FREDERICK = { lat: 39.3847, lon: -77.4055, label: "Tesla Supercharger" };
const TORONTO = { lat: 43.6479, lon: -79.3835, label: "Toronto" };
const TRIP: TripState = { ...EMPTY_TRIP, origin: FREDERICK, destination: TORONTO };
const vehicle = { id: "veh-1", title: "2026 Toyota bZ", connectors: ["NACS" as const] };

function route(index: number, distanceKm: number): DirectionsRoute {
  return {
    index,
    geometry_polyline6: `geometry-${index}`,
    distance_m: distanceKm * 1000,
    duration_s: 30_000 + index * 600,
    summary: null,
    countries: ["US", "CA"],
  };
}
const ROUTES = [route(0, 748), route(1, 805)];

function station(id: string, patch: Partial<RouteStation> = {}): RouteStation {
  return {
    ...makeStation({ id, name: `Station ${id}` }),
    distance_along_route_km: 12.5,
    distance_from_route_km: 0.7,
    ...patch,
  };
}

function ok(items: RouteStation[], patch: Partial<Extract<AlongRouteResult, { items: unknown }>> = {}): AlongRouteResult {
  return { items, totalResults: items.length, unknownPowerCount: 0, truncated: false, ...patch };
}

/** A promise the test resolves by hand, to control the order of responses. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

function renderPlanner(props: Partial<Parameters<typeof TripPlanner>[0]> = {}) {
  return render(
    <TripPlanner
      initial={TRIP}
      units="imperial"
      vehicle={vehicle}
      preferredNetworks={[]}
      {...props}
    />,
  );
}

function currentUrlState(): TripState {
  return parseTripState(new URLSearchParams(window.location.search));
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/trips");
  Element.prototype.scrollIntoView = vi.fn();
  actions.getDirections.mockResolvedValue({ routes: ROUTES, noRouteReason: null } satisfies DirectionsResult);
  actions.getStationsAlongRoute.mockImplementation(async ({ geometry }: { geometry: string }) =>
    geometry === "geometry-0"
      ? ok([station("a1"), station("a2", { compatible: false })])
      : ok([station("b1")]),
  );
});

describe("TripPlanner", () => {
  it("asks for both endpoints first and always shows the next-release note", () => {
    renderPlanner({ initial: EMPTY_TRIP });
    expect(screen.getByText(/Choose where you're starting/)).toBeInTheDocument();
    expect(screen.getByTestId("next-release-note")).toHaveTextContent(
      "Charging stops and arrival battery are planned in the next release",
    );
    expect(actions.getDirections).not.toHaveBeenCalled();
  });

  it("draws every route, lists them and loads the selected route's stations first", async () => {
    const first = deferred<AlongRouteResult>();
    actions.getStationsAlongRoute.mockImplementationOnce(() => first.promise);
    renderPlanner();

    await screen.findByRole("button", { name: /Route A/ });
    expect(actions.getDirections).toHaveBeenCalledWith({
      origin: { lat: FREDERICK.lat, lon: FREDERICK.lon },
      destination: { lat: TORONTO.lat, lon: TORONTO.lon },
    });
    // Only the selected route is asked for until its stations arrive (D15).
    expect(actions.getStationsAlongRoute).toHaveBeenCalledTimes(1);
    expect(actions.getStationsAlongRoute.mock.calls[0][0]).toEqual({
      geometry: "geometry-0",
      filters: {
        corridor_km: 3,
        connector: ["NACS"],
        charging_level: "dc_fast",
        vehicle_id: "veh-1",
        limit: 200,
      },
    });

    await act(async () => first.resolve(ok([station("a1"), station("a2", { compatible: false })])));
    await waitFor(() => expect(actions.getStationsAlongRoute).toHaveBeenCalledTimes(2));
    expect(actions.getStationsAlongRoute.mock.calls[1][0].geometry).toBe("geometry-1");

    const routeA = screen.getByRole("button", { name: /Route A/ });
    expect(routeA).toHaveTextContent("Shortest");
    // Only compatible stations count in the route summary.
    expect(within(routeA).getByTestId("route-chargers")).toHaveTextContent(
      "1 compatible charger within 1.9 mi",
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole("button", { name: /Route B/ })).getByTestId("route-chargers"),
      ).toHaveTextContent("1 compatible charger within 1.9 mi"),
    );

    expect(screen.getByTestId("route-station-count")).toHaveTextContent(
      "2 stations within 1.9 mi of Route A",
    );
    const list = screen.getByRole("region", { name: "Chargers along the route" });
    expect(within(list).getAllByRole("article")).toHaveLength(2);
    expect(within(list).getByRole("article", { name: "Station a1" })).toHaveTextContent(
      "7.8 mi along · 0.4 mi off route",
    );
    expect(mapProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        routes: [
          { index: 0, geometry_polyline6: "geometry-0" },
          { index: 1, geometry_polyline6: "geometry-1" },
        ],
        selectedRouteIndex: 0,
        stations: [expect.objectContaining({ id: "a1" }), expect.objectContaining({ id: "a2" })],
      }),
    );
  });

  it("selecting a route shows its stations from cache and puts it in the URL", async () => {
    renderPlanner();
    await screen.findByText("2 stations within 1.9 mi of Route A");
    await waitFor(() => expect(actions.getStationsAlongRoute).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: /Route B/ }));
    expect(await screen.findByText("1 station within 1.9 mi of Route B")).toBeInTheDocument();
    expect(actions.getStationsAlongRoute).toHaveBeenCalledTimes(2);
    expect(currentUrlState()).toMatchObject({ routeIndex: 1, origin: FREDERICK, destination: TORONTO });
    expect(mapProps).toHaveBeenLastCalledWith(expect.objectContaining({ selectedRouteIndex: 1 }));
  });

  it("a route picked on the map is selected too", async () => {
    renderPlanner();
    await screen.findByText("2 stations within 1.9 mi of Route A");
    const { onSelectRoute } = mapProps.mock.lastCall![0] as MapViewProps;
    act(() => onSelectRoute?.(1));
    expect(screen.getByRole("button", { name: /Route B/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("refetches stations when a filter changes, without asking for directions again", async () => {
    renderPlanner();
    await screen.findByText("2 stations within 1.9 mi of Route A");
    await waitFor(() => expect(actions.getStationsAlongRoute).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: "CCS1" }));
    await waitFor(() => expect(actions.getStationsAlongRoute).toHaveBeenCalledTimes(4));
    expect(actions.getStationsAlongRoute.mock.calls[2][0].filters.connector).toEqual(["CCS1", "NACS"]);
    expect(actions.getDirections).toHaveBeenCalledTimes(1);
    expect(currentUrlState().filters.connectors).toEqual(["CCS1", "NACS"]);
  });

  it("swaps origin and destination and plans again", async () => {
    renderPlanner();
    await screen.findByRole("button", { name: /Route A/ });
    fireEvent.click(screen.getByRole("button", { name: "Swap origin and destination" }));
    await waitFor(() => expect(actions.getDirections).toHaveBeenCalledTimes(2));
    expect(actions.getDirections.mock.calls[1][0]).toEqual({
      origin: { lat: TORONTO.lat, lon: TORONTO.lon },
      destination: { lat: FREDERICK.lat, lon: FREDERICK.lon },
    });
    expect(currentUrlState()).toMatchObject({ origin: TORONTO, destination: FREDERICK });
  });

  it.each([
    ["no_route", "No drivable route connects these places."],
    ["no_road_near_point", "One of these places isn't near a road."],
  ] as const)("shows the no-route reason %s", async (reason, text) => {
    actions.getDirections.mockResolvedValue({ routes: [], noRouteReason: reason });
    renderPlanner();
    expect(await screen.findByText(new RegExp(text))).toBeInTheDocument();
    expect(actions.getStationsAlongRoute).not.toHaveBeenCalled();
  });

  it("shows a Mapbox outage with a retry", async () => {
    actions.getDirections.mockResolvedValueOnce({ error: "MAPBOX_UNREACHABLE" });
    renderPlanner();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The map provider isn't responding right now.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: /Route A/ })).toBeInTheDocument();
    expect(actions.getDirections).toHaveBeenCalledTimes(2);
  });

  it("still shows routes when the station provider is down, with a retry", async () => {
    actions.getStationsAlongRoute.mockResolvedValue({ error: "STATIONS_UNREACHABLE" });
    renderPlanner();
    expect(await screen.findByText("The station provider isn't responding right now.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Route A/ })).toHaveTextContent("Chargers unavailable");

    actions.getStationsAlongRoute.mockResolvedValue(ok([station("a1")]));
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("1 station within 1.9 mi of Route A")).toBeInTheDocument();
  });

  it("without a vehicle: a banner, every station counted, nothing marked compatible", async () => {
    actions.getStationsAlongRoute.mockResolvedValue(
      ok([station("a1", { compatible: null }), station("a2", { compatible: null })]),
    );
    renderPlanner({ vehicle: null });
    expect(screen.getByText(/aren't checked for compatibility/)).toBeInTheDocument();
    const routeA = await screen.findByRole("button", { name: /Route A/ });
    await waitFor(() =>
      expect(within(routeA).getByTestId("route-chargers")).toHaveTextContent("2 chargers within"),
    );
    const call = actions.getStationsAlongRoute.mock.calls[0][0];
    expect(call.filters.vehicle_id).toBeUndefined();
    expect(call.filters.connector).toBeUndefined();
  });

  it("restores the selected route from the URL state", async () => {
    renderPlanner({ initial: { ...TRIP, routeIndex: 1 } });
    expect(await screen.findByText("1 station within 1.9 mi of Route B")).toBeInTheDocument();
    expect(actions.getStationsAlongRoute.mock.calls[0][0].geometry).toBe("geometry-1");
  });

  it("falls back to the first route when the URL's route doesn't exist", async () => {
    actions.getDirections.mockResolvedValue({ routes: [ROUTES[0]], noRouteReason: null });
    renderPlanner({ initial: { ...TRIP, routeIndex: 2 } });
    expect(await screen.findByText("2 stations within 1.9 mi of Route A")).toBeInTheDocument();
  });

  it("says when the provider had more stations than shown", async () => {
    actions.getStationsAlongRoute.mockResolvedValue(ok([station("a1")], { truncated: true, unknownPowerCount: 1 }));
    renderPlanner();
    expect(await screen.findByText(/The provider has more stations than shown/)).toBeInTheDocument();
    expect(screen.getByTestId("route-station-count")).toHaveTextContent("· 1 with unknown power");
  });
});
