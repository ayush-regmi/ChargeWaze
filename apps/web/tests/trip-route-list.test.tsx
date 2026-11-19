import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DirectionsRoute } from "@/app/(app)/trips/_components/actions";
import {
  RouteList,
  chargersText,
  formatDuration,
  routeLabel,
  shortestRouteIndex,
} from "@/app/(app)/trips/_components/route-list";

vi.mock("@/app/(app)/trips/_components/actions", () => ({}));

function route(patch: Partial<DirectionsRoute>): DirectionsRoute {
  return {
    index: 0,
    geometry_polyline6: "_p~iF~ps|U_ulLnnqC",
    distance_m: 748_107.9,
    duration_s: 30_890.7,
    summary: "US 15, QEW",
    countries: ["US", "CA"],
    ...patch,
  };
}

const ROUTES = [
  route({ index: 0, distance_m: 805_000, duration_s: 31_103.7, summary: "US 15, I 99 North" }),
  route({ index: 1, distance_m: 748_107.9, duration_s: 30_890.7 }),
];

describe("route list helpers", () => {
  it("labels routes A/B/C", () => {
    expect([0, 1, 2].map(routeLabel)).toEqual(["Route A", "Route B", "Route C"]);
  });

  it("formats durations", () => {
    expect(formatDuration(30_890.7)).toBe("8 h 35 min");
    expect(formatDuration(2_700)).toBe("45 min");
    expect(formatDuration(7_200)).toBe("2 h");
  });

  it("marks the shortest only when there is a choice", () => {
    expect(shortestRouteIndex(ROUTES)).toBe(1);
    expect(shortestRouteIndex([ROUTES[0]])).toBeNull();
  });

  it("describes the chargers on a route", () => {
    const opts = { hasVehicle: true, corridorText: "1.9 mi" };
    expect(chargersText(undefined, opts)).toBe("Finding chargers…");
    expect(chargersText({ status: "error" }, opts)).toBe("Chargers unavailable");
    expect(chargersText({ status: "ok", count: 12, truncated: false }, opts)).toBe(
      "12 compatible chargers within 1.9 mi",
    );
    expect(chargersText({ status: "ok", count: 1, truncated: true }, opts)).toBe(
      "1+ compatible charger within 1.9 mi",
    );
    expect(
      chargersText({ status: "ok", count: 3, truncated: false }, { ...opts, hasVehicle: false }),
    ).toBe("3 chargers within 1.9 mi");
  });
});

describe("RouteList", () => {
  it("shows distance and duration in the user's units, the shortest mark and the border", () => {
    const onSelect = vi.fn();
    render(
      <RouteList
        routes={ROUTES}
        selectedIndex={0}
        onSelect={onSelect}
        units="metric"
        summaries={{ 0: { status: "ok", count: 5, truncated: false }, 1: { status: "loading" } }}
        hasVehicle
        corridorText="3.0 km"
      />,
    );
    const a = screen.getByRole("button", { name: /Route A/ });
    const b = screen.getByRole("button", { name: /Route B/ });
    expect(a).toHaveAttribute("aria-pressed", "true");
    expect(b).toHaveAttribute("aria-pressed", "false");
    expect(a).toHaveTextContent("805 km · 8 h 38 min");
    expect(a).toHaveTextContent("via US 15, I 99 North · US → CA");
    expect(a).toHaveTextContent("5 compatible chargers within 3.0 km");
    expect(a).not.toHaveTextContent("Shortest");
    expect(b).toHaveTextContent("Shortest");
    expect(b).toHaveTextContent("Finding chargers…");

    fireEvent.click(b);
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it("uses miles for imperial users", () => {
    render(
      <RouteList
        routes={[ROUTES[1]]}
        selectedIndex={1}
        onSelect={() => {}}
        units="imperial"
        summaries={{}}
        hasVehicle={false}
        corridorText="1.9 mi"
      />,
    );
    expect(screen.getByRole("button", { name: /Route B/ })).toHaveTextContent("465 mi · 8 h 35 min");
  });
});
