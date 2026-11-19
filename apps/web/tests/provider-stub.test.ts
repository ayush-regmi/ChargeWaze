import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  E2E_AFDC_API_KEY,
  loadFixtures,
  matches,
  paginateAfdc,
  parseLineString,
  parseWaypoints,
} from "@/e2e/support/provider-stub-lib.mjs";
import { decodePolyline } from "@/lib/polyline";

const FIXTURES_DIR = path.resolve(__dirname, "../e2e/support/provider-fixtures");
const fixtures = loadFixtures(FIXTURES_DIR);

type Request = { method: string; pathname: string; params: URLSearchParams };

function find(req: Request) {
  return fixtures.find((f) => matches(f, req))?.file ?? null;
}

/** A WKT route like the AFDC adapter sends: longitude first, thinned out. */
function wktFor(routeIndex: number): string {
  const directions = JSON.parse(
    readFileSync(path.resolve(__dirname, "../../../fixtures/mapbox/directions_frederick_toronto.json"), "utf8"),
  );
  const points = decodePolyline(directions.routes[routeIndex].geometry).filter((_, i, all) => i % 40 === 0 || i === all.length - 1);
  return `LINESTRING(${points.map(([lat, lon]) => `${lon} ${lat}`).join(", ")})`;
}

function nearbyRoute(routeIndex: number): Request {
  return {
    method: "POST",
    pathname: "/afdc/nearby-route.json",
    params: new URLSearchParams({ route: wktFor(routeIndex), api_key: E2E_AFDC_API_KEY, limit: "200" }),
  };
}

describe("provider stub fixtures", () => {
  it("every fixture file loads, with its body", () => {
    expect(fixtures.length).toBeGreaterThanOrEqual(7);
    for (const f of fixtures) expect(f.body).toBeTruthy();
  });

  it("route A and route B each get their own AFDC fixture", () => {
    expect(find(nearbyRoute(0))).toBe("31-afdc-nearby-route-a.json");
    expect(find(nearbyRoute(1))).toBe("30-afdc-nearby-route-b.json");
  });

  it("matches Directions by waypoints, rounded or not", () => {
    const req = (coords: string): Request => ({
      method: "GET",
      pathname: `/mapbox/directions/v5/mapbox/driving/${coords}`,
      params: new URLSearchParams(),
    });
    expect(find(req("-77.405518,39.384655;-79.383550,43.647938"))).toBe(
      "20-mapbox-directions-frederick-toronto.json",
    );
    expect(find(req("-77.4055,39.3847;-79.3835,43.6479"))).toBe(
      "20-mapbox-directions-frederick-toronto.json",
    );
    // Swapped endpoints are a different trip: no fixture, so the stub 404s.
    expect(find(req("-79.383550,43.647938;-77.405518,39.384655"))).toBeNull();
  });

  it("matches suggest by search text, case-insensitively", () => {
    const suggest = (q: string): Request => ({
      method: "GET",
      pathname: "/mapbox/search/searchbox/v1/suggest",
      params: new URLSearchParams({ q }),
    });
    expect(find(suggest("TORONTO"))).toBe("10-mapbox-suggest-toronto.json");
    expect(find(suggest("Tesla Supercharger Frederick"))).toBe("10-mapbox-suggest-frederick.json");
    expect(find(suggest("Ottawa"))).toBeNull();
  });

  it("checks the method", () => {
    expect(find({ ...nearbyRoute(0), method: "GET" })).toBeNull();
  });
});

describe("provider stub helpers", () => {
  it("parses WKT and Directions waypoints", () => {
    expect(parseLineString("LINESTRING(-77.4 39.4, -79.3 43.6)")).toEqual([
      { lat: 39.4, lon: -77.4 },
      { lat: 43.6, lon: -79.3 },
    ]);
    expect(parseLineString("POINT(1 2)")).toBeNull();
    expect(parseWaypoints("/mapbox/directions/v5/mapbox/driving/-77.4,39.4%3B-79.3,43.6")).toEqual([
      { lat: 39.4, lon: -77.4 },
      { lat: 43.6, lon: -79.3 },
    ]);
  });

  it("pages AFDC results by offset and limit, keeping total_results", () => {
    const body = { fuel_stations: [1, 2, 3, 4, 5], total_results: 5 };
    expect(paginateAfdc(body, new URLSearchParams("offset=2&limit=2"))).toEqual({
      fuel_stations: [3, 4],
      total_results: 5,
    });
    expect(paginateAfdc(body, new URLSearchParams()).fuel_stations).toHaveLength(5);
    expect(paginateAfdc(body, new URLSearchParams("offset=5")).fuel_stations).toEqual([]);
  });
});
