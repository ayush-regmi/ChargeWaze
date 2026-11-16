import { describe, expect, it } from "vitest";

import {
  KM_PER_MILE,
  formatDistance,
  formatPercent,
  formatPowerKw,
  kmToMiles,
  milesToKm,
} from "@/lib/units";

describe("km ↔ miles", () => {
  it("converts both ways and round-trips", () => {
    expect(kmToMiles(KM_PER_MILE)).toBeCloseTo(1);
    expect(milesToKm(1)).toBeCloseTo(1.609344);
    expect(kmToMiles(milesToKm(123.4))).toBeCloseTo(123.4);
    expect(kmToMiles(0)).toBe(0);
  });
});

describe("formatDistance", () => {
  it("shows miles for imperial and km for metric", () => {
    expect(formatDistance(296, "imperial")).toEqual({ value: "184", unit: "mi" });
    expect(formatDistance(296, "metric")).toEqual({ value: "296", unit: "km" });
  });

  it("keeps missing values missing", () => {
    expect(formatDistance(null, "metric")).toBeNull();
    expect(formatDistance(undefined, "imperial")).toBeNull();
    expect(formatDistance(Number.NaN, "metric")).toBeNull();
  });

  it("does not treat zero as missing", () => {
    expect(formatDistance(0, "imperial")).toEqual({ value: "0", unit: "mi" });
  });
});

describe("formatPercent / formatPowerKw", () => {
  it("formats values with units", () => {
    expect(formatPercent(72.4)).toEqual({ value: "72", unit: "%" });
    expect(formatPowerKw(7.25)).toEqual({ value: (7.3).toLocaleString(), unit: "kW" });
  });

  it("returns null for missing values", () => {
    expect(formatPercent(null)).toBeNull();
    expect(formatPowerKw(null)).toBeNull();
    expect(formatPowerKw(undefined)).toBeNull();
  });
});
