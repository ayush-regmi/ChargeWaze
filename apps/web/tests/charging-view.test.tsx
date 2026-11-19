import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  parseChargingView,
  withView,
  type FavoriteStation,
} from "@/app/(app)/charging/_components/charging-tab";
import { ChargingView } from "@/app/(app)/charging/_components/charging-view";
import type { MapViewProps } from "@/components/map/types";
import { EMPTY_FILTERS, parseFilters, type ChargingFilters } from "@/lib/stations";

import { makeStation } from "./station-fixture";

const router = { push: vi.fn(), replace: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const mapProps = vi.fn();
vi.mock("@/components/map/lazy-map-view", () => ({
  LazyMapView: (props: MapViewProps) => {
    mapProps(props);
    return <div data-testid="map-stub" />;
  },
}));
vi.mock("@/components/stations/favorite-actions", () => ({
  setStationFavorite: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@/components/places/actions", () => ({
  suggestPlaces: vi.fn(),
  retrievePlace: vi.fn(),
}));

const FREDERICK = { lat: 39.4143, lon: -77.4105, label: "Frederick, MD" };
const vehicle = { id: "veh-1", title: "2026 Toyota bZ", connectors: ["NACS" as const] };

function renderView(props: Partial<Parameters<typeof ChargingView>[0]> = {}) {
  return render(
    <ChargingView
      filters={{ ...EMPTY_FILTERS, location: FREDERICK }}
      units="imperial"
      vehicle={vehicle}
      result={{ items: [], more: false, unknownPowerCount: 0 }}
      error={null}
      networkOptions={[]}
      {...props}
    />,
  );
}

/** The filters the last navigation asked for, parsed back from its URL. */
function lastNavigatedFilters(): ChargingFilters {
  const calls = [...router.replace.mock.calls, ...router.push.mock.calls];
  const url = String(calls.at(-1)?.[0]);
  return parseFilters(new URL(url, "http://x").searchParams);
}

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
});

describe("ChargingView", () => {
  it("asks for a location first; there is no default location", () => {
    renderView({ filters: EMPTY_FILTERS, result: null });
    expect(screen.getByText(/Search for a place or use your location/)).toBeInTheDocument();
    expect(screen.queryByTestId("map-stub")).not.toBeInTheDocument();
  });

  it("lists stations nearest first with distance in the user's units", () => {
    const near = makeStation({ id: "near", name: "Near", location: { lat: 39.415, lon: -77.41 } });
    const far = makeStation({ id: "far", name: "Far", location: { lat: 39.6, lon: -77.41 } });
    renderView({ result: { items: [far, near], more: false, unknownPowerCount: 1 } });

    const list = screen.getByRole("region", { name: "Stations" });
    const cards = within(list).getAllByRole("article");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Near", "Far"]);
    expect(cards[1]).toHaveTextContent(/1\d mi/);
    expect(list).toHaveTextContent("2 stations near Frederick, MD · 1 with unknown power");
    // Detail links keep the search, so Back returns to it.
    expect(within(cards[0]).getByRole("link")).toHaveAttribute(
      "href",
      "/charging/near?lat=39.4143&lon=-77.4105&place=Frederick%2C+MD",
    );
    expect(mapProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        center: FREDERICK,
        stations: [
          expect.objectContaining({ id: "near" }),
          expect.objectContaining({ id: "far" }),
        ],
      }),
    );
  });

  it("selecting on the map highlights the card", () => {
    renderView({ result: { items: [makeStation({ id: "s1", name: "S1" })], more: false, unknownPowerCount: 0 } });
    const { onSelect } = mapProps.mock.lastCall![0] as MapViewProps;
    act(() => onSelect?.("s1"));
    expect(screen.getByRole("article", { name: "S1" })).toHaveAttribute("aria-current", "true");
  });

  it("puts filter changes in the URL, defaulting to the vehicle's connectors", () => {
    renderView();
    expect(screen.getByRole("button", { name: "NACS (Tesla)", pressed: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "CCS1" }));
    expect(router.replace).toHaveBeenCalledTimes(1);
    expect(lastNavigatedFilters()).toEqual({
      ...EMPTY_FILTERS,
      location: FREDERICK,
      connectors: ["CCS1", "NACS"],
    });

    fireEvent.change(screen.getByLabelText("Charging level"), { target: { value: "dc_fast" } });
    expect(lastNavigatedFilters()).toMatchObject({ level: "dc_fast", location: FREDERICK });
  });

  it("keeps an earlier edit whose navigation hasn't finished (review fix)", () => {
    // The mocked router never navigates, so the `filters` prop stays stale,
    // as it does in the app while the server navigation is in flight.
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "CCS1" }));
    fireEvent.change(screen.getByLabelText("Charging level"), { target: { value: "dc_fast" } });

    expect(router.replace).toHaveBeenCalledTimes(2);
    const second = parseFilters(
      new URL(String(router.replace.mock.calls[1][0]), "http://x").searchParams,
    );
    expect(second).toEqual({
      ...EMPTY_FILTERS,
      location: FREDERICK,
      connectors: ["CCS1", "NACS"],
      level: "dc_fast",
    });
    // The controls and the detail links show the merged state too.
    expect(screen.getByRole("button", { name: "CCS1", pressed: true })).toBeInTheDocument();
    expect(screen.getByLabelText("Charging level")).toHaveValue("dc_fast");
  });

  it("keeps pending filter edits when the location changes", () => {
    renderView();
    fireEvent.change(screen.getByLabelText("Minimum power"), { target: { value: "150" } });
    // "Use my location" goes through the browser's geolocation API.
    const getCurrentPosition = vi.fn((ok: PositionCallback) =>
      ok({ coords: { latitude: 40.1, longitude: -76.9 } } as GeolocationPosition),
    );
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: { getCurrentPosition },
    });
    fireEvent.click(screen.getByRole("button", { name: "Use my location" }));
    expect(router.push).toHaveBeenCalledTimes(1);
    expect(lastNavigatedFilters()).toMatchObject({
      minPowerKw: 150,
      location: { lat: 40.1, lon: -76.9, label: "My location" },
    });
  });

  it("resyncs to the URL on back/forward but skips its own intermediate navigations", () => {
    const { rerender } = renderView();
    const view = (filters: ChargingFilters) => (
      <ChargingView
        filters={filters}
        units="imperial"
        vehicle={vehicle}
        result={{ items: [], more: false, unknownPowerCount: 0 }}
        error={null}
        networkOptions={[]}
      />
    );
    const base = { ...EMPTY_FILTERS, location: FREDERICK };
    fireEvent.click(screen.getByRole("button", { name: "CCS1" }));
    fireEvent.change(screen.getByLabelText("Charging level"), { target: { value: "dc_fast" } });

    // The first navigation lands while the second is still pending: keep the second.
    rerender(view({ ...base, connectors: ["CCS1", "NACS"] }));
    expect(screen.getByLabelText("Charging level")).toHaveValue("dc_fast");

    // The second lands.
    rerender(view({ ...base, connectors: ["CCS1", "NACS"], level: "dc_fast" }));
    expect(screen.getByLabelText("Charging level")).toHaveValue("dc_fast");

    // Back/forward to an unrelated URL wins over local state.
    rerender(view({ ...base, level: "level2" }));
    expect(screen.getByLabelText("Charging level")).toHaveValue("level2");
    expect(screen.getByRole("button", { name: "CCS1", pressed: false })).toBeInTheDocument();
  });

  it("hints to connect a vehicle when there is none", () => {
    renderView({ vehicle: null });
    expect(screen.getByRole("link", { name: "Connect a vehicle" })).toHaveAttribute("href", "/garage");
  });

  it("hints to add connectors when the vehicle has none set", () => {
    renderView({ vehicle: { ...vehicle, connectors: [] } });
    expect(screen.getByRole("link", { name: "Add them in the Garage" })).toHaveAttribute(
      "href",
      "/garage/veh-1",
    );
  });

  it("shows the provider error with a retry (E6)", () => {
    renderView({ result: null, error: "STATIONS_UNREACHABLE" });
    expect(screen.getByRole("alert")).toHaveTextContent("isn't responding");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(router.refresh).toHaveBeenCalled();
  });

  it("shows the empty state and the truncation note", () => {
    renderView({ result: { items: [], more: false, unknownPowerCount: 0 } });
    expect(screen.getByText(/No stations match these filters/)).toBeInTheDocument();
  });

  it("says when only the nearest stations are shown", () => {
    renderView({ result: { items: [makeStation()], more: true, unknownPowerCount: 0 } });
    expect(screen.getByText(/Showing the nearest 1/)).toBeInTheDocument();
  });

  it("stars the nearby stations that are favorites", () => {
    const fav = makeStation({ id: "fav", name: "Fav" });
    const other = makeStation({ id: "other", name: "Other" });
    renderView({
      result: { items: [fav, other], more: false, unknownPowerCount: 0 },
      favorites: [favorite(fav)],
    });
    expect(screen.getByRole("button", { name: "Remove Fav from favorites" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add Other to favorites" })).toBeInTheDocument();
  });

  it("shows no stars when the favorites couldn't be loaded", () => {
    renderView({
      result: { items: [makeStation()], more: false, unknownPowerCount: 0 },
      favorites: null,
    });
    expect(screen.queryByRole("button", { name: /favorites/ })).not.toBeInTheDocument();
  });
});

function favorite(station = makeStation(), favorited_at = "2026-11-19T12:00:00Z"): FavoriteStation {
  return { ...station, favorited_at };
}

describe("ChargingView Favorites tab", () => {
  it("links both tabs and keeps the filters", () => {
    renderView();
    const tabs = screen.getByRole("navigation", { name: "Charging views" });
    const nearby = within(tabs).getByRole("link", { name: "Nearby" });
    const favs = within(tabs).getByRole("link", { name: "Favorites" });
    expect(nearby).toHaveAttribute("aria-current", "page");
    expect(favs).not.toHaveAttribute("aria-current");
    expect(favs.getAttribute("href")).toMatch(/^\/charging\?lat=.*&view=favorites$/);
    expect(parseFilters(new URL(favs.getAttribute("href")!, "http://x").searchParams).location)
      .toMatchObject({ label: "Frederick, MD" });
  });

  it("lists favorites newest first without a place, with no search or filters", () => {
    renderView({
      view: "favorites",
      filters: EMPTY_FILTERS,
      result: null,
      favorites: [
        favorite(makeStation({ id: "b", name: "Newer" })),
        favorite(makeStation({ id: "a", name: "Older" })),
      ],
    });
    expect(
      within(screen.getByRole("navigation", { name: "Charging views" })).getByRole("link", {
        name: "Favorites",
      }),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("combobox", { name: "Search near" })).not.toBeInTheDocument();
    const list = screen.getByRole("region", { name: "Favorite stations" });
    expect(within(list).getAllByRole("article").map((a) => a.getAttribute("aria-label"))).toEqual([
      "Newer",
      "Older",
    ]);
    expect(within(list).getByRole("status")).toHaveTextContent("2 favorite stations");
    expect(within(list).getAllByRole("button", { name: /Remove .* from favorites/ })).toHaveLength(2);
    expect(mapProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ stations: expect.arrayContaining([expect.objectContaining({ id: "a" })]) }),
    );
  });

  it("sorts favorites by distance when a place is set", () => {
    renderView({
      view: "favorites",
      favorites: [
        favorite(makeStation({ id: "far", name: "Far", location: { lat: 39.6, lon: -77.41 } })),
        favorite(makeStation({ id: "near", name: "Near", location: { lat: 39.415, lon: -77.41 } })),
      ],
    });
    const list = screen.getByRole("region", { name: "Favorite stations" });
    const cards = within(list).getAllByRole("article");
    expect(cards.map((a) => a.getAttribute("aria-label"))).toEqual(["Near", "Far"]);
    expect(cards[0]).toHaveTextContent(/mi/);
  });

  it("refreshes after unstarring so the station drops out", async () => {
    renderView({ view: "favorites", favorites: [favorite(makeStation({ name: "Fav" }))] });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Remove Fav from favorites" }));
    });
    expect(router.refresh).toHaveBeenCalled();
  });

  it("has an empty state", () => {
    renderView({ view: "favorites", favorites: [] });
    expect(screen.getByText(/No favorite stations yet/)).toBeInTheDocument();
    expect(screen.queryByTestId("map-stub")).not.toBeInTheDocument();
  });

  it("says when the favorites couldn't be loaded", () => {
    renderView({ view: "favorites", favorites: null });
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load your favorite stations");
  });
});

describe("Charging view URL helpers", () => {
  it("parses only `view=favorites` as the Favorites tab", () => {
    expect(parseChargingView({ view: "favorites" })).toBe("favorites");
    expect(parseChargingView({ view: "nope" })).toBe("nearby");
    expect(parseChargingView({ view: ["favorites", "x"] })).toBe("nearby");
    expect(parseChargingView({})).toBe("nearby");
  });

  it("writes the tab only when it isn't the default", () => {
    expect(withView("/charging", "nearby")).toBe("/charging");
    expect(withView("/charging", "favorites")).toBe("/charging?view=favorites");
    expect(withView("/charging?lat=1", "favorites")).toBe("/charging?lat=1&view=favorites");
  });
});
