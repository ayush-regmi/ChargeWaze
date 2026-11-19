import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// jsdom has no WebGL: the real mapbox-gl is never loaded in unit tests.
vi.mock("mapbox-gl", () => ({ default: {} }));
vi.mock("mapbox-gl/dist/mapbox-gl.css", () => ({}));

import { LazyMapView } from "@/components/map/lazy-map-view";
import { boundsOf, routeFeatures, segmentFeatures, stationFeatures } from "@/components/map/map-view";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("LazyMapView", () => {
  it("shows 'Map unavailable' without a public token", () => {
    vi.stubEnv("NEXT_PUBLIC_MAPBOX_PUBLIC_TOKEN", "");
    render(<LazyMapView stations={[]} routes={[]} selectedRouteIndex={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("Map unavailable");
    expect(screen.getByRole("status")).toHaveTextContent("The list still works.");
  });
});

describe("map features", () => {
  // "_p~iF~ps|U_ulLnnqC" at precision 6 is two valid points.
  const routes = [
    { index: 0, geometry_polyline6: "_p~iF~ps|U_ulLnnqC" },
    { index: 1, geometry_polyline6: "not a polyline!" },
    { index: 2, geometry_polyline6: "_p~iF~ps|U_ulLnnqC" },
  ];

  it("draws decodable routes and marks the selected one", () => {
    const features = routeFeatures(routes, 2);
    expect(features.map((f) => f.properties)).toEqual([
      { index: 0, selected: false },
      { index: 2, selected: true },
    ]);
    expect(features[0].geometry).toEqual({
      type: "LineString",
      coordinates: [
        [-12.02, 3.85],
        [-12.095, 4.07],
      ],
    });
  });

  it("maps stations to points coloured by compatibility", () => {
    const features = stationFeatures(
      [
        { id: "a", location: { lat: 1, lon: 2 }, compatible: true },
        { id: "b", location: { lat: 3, lon: 4 }, compatible: false },
        { id: "c", location: { lat: 5, lon: 6 } },
      ],
      "b",
    );
    expect(features.map((f) => [f.properties.id, f.properties.compat, f.properties.selected])).toEqual([
      ["a", "yes", false],
      ["b", "no", true],
      ["c", "unknown", false],
    ]);
    expect(features[0].geometry).toEqual({ type: "Point", coordinates: [2, 1] });
  });

  it("marks plan stops and carries their labels", () => {
    const [stop, plain] = stationFeatures(
      [
        { id: "s", location: { lat: 1, lon: 2 }, kind: "stop", label: "16% → 61%" },
        { id: "p", location: { lat: 3, lon: 4 } },
      ],
      null,
    );
    expect([stop.properties.stop, stop.properties.label]).toEqual([true, "16% → 61%"]);
    expect([plain.properties.stop, plain.properties.label]).toEqual([false, ""]);
  });

  it("draws segments as lines and skips ones with fewer than two points", () => {
    const features = segmentFeatures([
      { id: "gap", coordinates: [{ lat: 1, lon: 2 }, { lat: 3, lon: 4 }] },
      { id: "dot", coordinates: [{ lat: 1, lon: 2 }] },
    ]);
    expect(features).toHaveLength(1);
    expect(features[0].geometry).toEqual({
      type: "LineString",
      coordinates: [
        [2, 1],
        [4, 3],
      ],
    });
  });

  it("computes bounds over everything drawn", () => {
    const f = [...routeFeatures(routes.slice(0, 1), null), ...stationFeatures([{ id: "a", location: { lat: 10, lon: -20 } }], null)];
    expect(boundsOf(f)).toEqual([
      [-20, 3.85],
      [-12.02, 10],
    ]);
    expect(boundsOf([])).toBeNull();
  });
});
