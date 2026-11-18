import { describe, expect, it } from "vitest";

import { distanceUnit, formatDistanceText, haversineKm, roundCoord } from "@/lib/geo-format";

describe("haversineKm", () => {
  it("is zero for the same point", () => {
    expect(haversineKm({ lat: 39.41, lon: -77.41 }, { lat: 39.41, lon: -77.41 })).toBe(0);
  });

  it("measures Frederick, MD → Toronto, ON as about 500 km straight-line", () => {
    const km = haversineKm({ lat: 39.4143, lon: -77.4105 }, { lat: 43.6532, lon: -79.3832 });
    expect(km).toBeGreaterThan(495);
    expect(km).toBeLessThan(505);
  });

  it("one degree of latitude is about 111 km", () => {
    expect(haversineKm({ lat: 0, lon: 0 }, { lat: 1, lon: 0 })).toBeCloseTo(111.19, 1);
  });
});

describe("formatDistanceText", () => {
  it("uses the user's units", () => {
    expect(formatDistanceText(16.09344, "imperial")).toBe("10 mi");
    expect(formatDistanceText(16.09344, "metric")).toBe("16 km");
  });

  it("keeps one decimal under 10 units", () => {
    expect(formatDistanceText(3.21869, "imperial")).toBe("2 mi");
    expect(formatDistanceText(2.46, "metric")).toBe("2.5 km");
  });

  it("leaves missing values missing", () => {
    expect(formatDistanceText(null, "metric")).toBeNull();
    expect(formatDistanceText(undefined, "imperial")).toBeNull();
    expect(formatDistanceText(Number.NaN, "metric")).toBeNull();
  });
});

describe("roundCoord / distanceUnit", () => {
  it("rounds to 4 decimals by default", () => {
    expect(roundCoord(39.4142987)).toBe(39.4143);
    expect(roundCoord(-77.41054)).toBe(-77.4105);
  });

  it("names the unit", () => {
    expect(distanceUnit("imperial")).toBe("mi");
    expect(distanceUnit("metric")).toBe("km");
  });
});
