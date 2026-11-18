import { describe, expect, it } from "vitest";

import {
  CONNECTOR_TYPES,
  EMPTY_FILTERS,
  addressLines,
  connectorDetail,
  defaultRadiusKm,
  directionsLinks,
  effectiveConnectors,
  parseFilters,
  radiusOptions,
  serializeFilters,
  sortByDistance,
  stationsErrorCode,
  toStationsQuery,
  toggleConnector,
  vehicleConnectorTypes,
  type ChargingFilters,
} from "@/lib/stations";

import { makeStation } from "./station-fixture";

describe("filters ↔ URL", () => {
  const full: ChargingFilters = {
    location: { lat: 39.4143, lon: -77.4105, label: "Frederick, MD" },
    connectors: ["CCS1", "NACS"],
    networks: ["Electrify America"],
    level: "dc_fast",
    minPowerKw: 150,
    radiusKm: 40.23,
  };

  it("round-trips every filter", () => {
    const qs = serializeFilters(full);
    expect(parseFilters(new URLSearchParams(qs))).toEqual(full);
  });

  it("round-trips through a Next.js searchParams object", () => {
    const parsed = parseFilters({
      lat: "39.4143",
      lon: "-77.4105",
      place: "Frederick, MD",
      connector: ["NACS", "CCS1"],
      network: "Electrify America",
      level: "dc_fast",
      min_kw: "150",
      radius: "40.23",
    });
    // Connector order is normalized.
    expect(parsed).toEqual(full);
  });

  it("leaves defaults out of the URL", () => {
    expect(serializeFilters(EMPTY_FILTERS)).toBe("");
  });

  it("rounds coordinates to 4 decimals in the URL", () => {
    const qs = serializeFilters({
      ...EMPTY_FILTERS,
      location: { lat: 39.41429871, lon: -77.41054321, label: null },
    });
    expect(qs).toBe("lat=39.4143&lon=-77.4105");
  });

  it("keeps the explicit 'any connector' choice", () => {
    const qs = serializeFilters({ ...EMPTY_FILTERS, connectors: "any" });
    expect(qs).toBe("connector=any");
    expect(parseFilters(new URLSearchParams(qs)).connectors).toBe("any");
  });

  it("drops invalid values instead of guessing", () => {
    const f = parseFilters(
      new URLSearchParams(
        "lat=200&lon=-77&connector=NEMA1450&level=fast&min_kw=-5&radius=500&network=",
      ),
    );
    expect(f).toEqual(EMPTY_FILTERS);
  });

  it("needs both lat and lon for a location", () => {
    expect(parseFilters(new URLSearchParams("lat=39.4")).location).toBeNull();
  });
});

describe("connector defaults", () => {
  it("defaults to the vehicle's connectors", () => {
    expect(effectiveConnectors(EMPTY_FILTERS, ["NACS", "CCS1"])).toEqual(["NACS", "CCS1"]);
  });

  it("an explicit choice overrides the vehicle", () => {
    expect(effectiveConnectors({ connectors: ["J1772"] }, ["CCS1"])).toEqual(["J1772"]);
    expect(effectiveConnectors({ connectors: "any" }, ["CCS1"])).toEqual([]);
  });

  it("toggling the last connector off means any connector", () => {
    expect(toggleConnector(["CCS1"], "CCS1")).toBe("any");
    expect(toggleConnector(["CCS1"], "NACS")).toEqual(["CCS1", "NACS"]);
  });

  it("keeps only station connector types from vehicle specs", () => {
    expect(vehicleConnectorTypes(["CCS1", "Type 2", "NACS"])).toEqual(["CCS1", "NACS"]);
    expect(vehicleConnectorTypes(null)).toEqual([]);
  });
});

describe("toStationsQuery", () => {
  const at = { ...EMPTY_FILTERS, location: { lat: 39.4, lon: -77.4, label: null } };

  it("is null without a location", () => {
    expect(
      toStationsQuery(EMPTY_FILTERS, { vehicleId: null, vehicleConnectors: [], units: "metric" }),
    ).toBeNull();
  });

  it("sends the vehicle and its connectors by default", () => {
    expect(
      toStationsQuery(at, { vehicleId: "v1", vehicleConnectors: ["CCS1"], units: "metric" }),
    ).toEqual({ lat: 39.4, lon: -77.4, radius_km: 25, limit: 100, connector: ["CCS1"], vehicle_id: "v1" });
  });

  it("sends every connector for 'any' so the API doesn't apply the vehicle default", () => {
    const q = toStationsQuery(
      { ...at, connectors: "any" },
      { vehicleId: "v1", vehicleConnectors: ["CCS1"], units: "imperial" },
    );
    expect(q?.connector).toEqual([...CONNECTOR_TYPES]);
    expect(q?.radius_km).toBe(defaultRadiusKm("imperial"));
  });

  it("sends no connector filter without a vehicle", () => {
    const q = toStationsQuery(at, { vehicleId: null, vehicleConnectors: [], units: "metric" });
    expect(q).not.toHaveProperty("connector");
    expect(q).not.toHaveProperty("vehicle_id");
  });

  it("passes level, network and power", () => {
    const q = toStationsQuery(
      { ...at, level: "level2", networks: ["EVgo"], minPowerKw: 50, radiusKm: 10 },
      { vehicleId: null, vehicleConnectors: [], units: "metric" },
    );
    expect(q).toMatchObject({ charging_level: "level2", network: ["EVgo"], min_power_kw: 50, radius_km: 10 });
  });
});

describe("radius options", () => {
  it("are in the user's units and never exceed the API's 80 km", () => {
    expect(radiusOptions("imperial").map((r) => r.label)).toEqual(["5 mi", "10 mi", "25 mi", "50 mi"]);
    expect(Math.max(...radiusOptions("imperial").map((r) => r.km))).toBe(80);
    expect(radiusOptions("metric").map((r) => r.km)).toEqual([10, 25, 50, 80]);
  });
});

describe("display helpers", () => {
  it("shows unknown count and power as unknown", () => {
    expect(connectorDetail({ count: 2, power_kw: 150 })).toBe("× 2 · 150 kW");
    expect(connectorDetail({ count: null, power_kw: null })).toBe("count unknown · power unknown");
    expect(connectorDetail({})).toBe("count unknown · power unknown");
  });

  it("builds address lines from the parts that exist", () => {
    expect(addressLines(makeStation())).toEqual(["1108 W 7th St", "Frederick, MD 21701", "US"]);
    expect(
      addressLines(
        makeStation({ street_address: null, city: null, region: "ON", postal_code: null, country: null }),
      ),
    ).toEqual(["ON"]);
  });

  it("links to external directions", () => {
    const l = directionsLinks({ lat: 39.42, lon: -77.41 });
    expect(l.google).toBe("https://www.google.com/maps/dir/?api=1&destination=39.42%2C-77.41");
    expect(l.apple).toBe("https://maps.apple.com/?daddr=39.42%2C-77.41");
  });

  it("sorts stations by distance from the centre", () => {
    const near = makeStation({ id: "a", location: { lat: 39.41, lon: -77.41 } });
    const far = makeStation({ id: "b", location: { lat: 39.6, lon: -77.41 } });
    const rows = sortByDistance([far, near], { lat: 39.4143, lon: -77.4105 });
    expect(rows.map((r) => r.station.id)).toEqual(["a", "b"]);
    expect(rows[1].distanceKm).toBeGreaterThan(20);
  });

  it("reads the API error code", () => {
    expect(stationsErrorCode({ error: { code: "STATIONS_UNREACHABLE", message: "" } })).toBe(
      "STATIONS_UNREACHABLE",
    );
    expect(stationsErrorCode({ error: { code: "INTERNAL_ERROR" } })).toBe("UNKNOWN");
    expect(stationsErrorCode(undefined)).toBe("UNKNOWN");
  });
});
