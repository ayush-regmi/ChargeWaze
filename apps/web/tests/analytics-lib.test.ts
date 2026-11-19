import { describe, expect, it } from "vitest";

import {
  formatTick,
  linearScale,
  niceStep,
  niceTicks,
} from "@/components/analytics/charts/scale";
import { segments } from "@/components/analytics/charts/line-chart";
import { errorReason, rangeErrorMessage } from "@/components/analytics/errors";
import {
  consumptionUnit,
  formatConsumption,
  formatDistanceValue,
  formatSignedKwh,
  qualityReasonLabel,
  reviewHref,
  toConsumption,
} from "@/components/analytics/format";
import {
  analyticsSearch,
  apiQuery,
  parseAnalyticsParams,
} from "@/components/analytics/params";

describe("linearScale", () => {
  it("maps the domain onto the range, inverted for a y-axis", () => {
    const y = linearScale([0, 100], [200, 0]);
    expect(y(0)).toBe(200);
    expect(y(50)).toBe(100);
    expect(y(100)).toBe(0);
  });

  it("puts every value at the midpoint of a zero-width domain", () => {
    expect(linearScale([3, 3], [0, 10])(3)).toBe(5);
  });
});

describe("niceTicks", () => {
  it("rounds the domain out to whole steps", () => {
    expect(niceTicks(0, 87)).toEqual({
      domain: [0, 100],
      ticks: [0, 20, 40, 60, 80, 100],
      step: 20,
    });
  });

  it("uses 2.5 steps when they fit best", () => {
    expect(niceStep(12, 5)).toBe(2.5);
    expect(niceTicks(0, 12).ticks).toEqual([0, 2.5, 5, 7.5, 10, 12.5]);
  });

  it("handles small fractional ranges without float noise", () => {
    expect(niceTicks(0, 0.3).ticks).toEqual([0, 0.1, 0.2, 0.3]);
    const t = niceTicks(0, 0.22);
    expect(t.step).toBe(0.05);
    expect(t.ticks).toEqual([0, 0.05, 0.1, 0.15, 0.2, 0.25]);
  });

  it("widens an empty or flat range", () => {
    expect(niceTicks(0, 0).domain).toEqual([0, 1]);
    expect(niceTicks(5, 5).domain[0]).toBe(0);
    expect(niceTicks(5, 5).domain[1]).toBeGreaterThanOrEqual(5);
  });

  it("treats non-finite input as zero", () => {
    expect(niceTicks(Number.NaN, Number.NEGATIVE_INFINITY).domain).toEqual([
      0, 1,
    ]);
  });
});

describe("formatTick", () => {
  it("uses as many decimals as the step needs", () => {
    expect(formatTick(20, 20)).toBe("20");
    expect(formatTick(7.5, 2.5)).toBe("7.5");
    expect(formatTick(0.1, 0.05)).toBe("0.10");
  });
});

describe("segments", () => {
  it("splits a series at missing values", () => {
    expect(segments([1, 2, null, 4, Number.NaN, 6])).toEqual([
      [
        { i: 0, v: 1 },
        { i: 1, v: 2 },
      ],
      [{ i: 3, v: 4 }],
      [{ i: 5, v: 6 }],
    ]);
  });
});

describe("consumption units", () => {
  it("converts kWh/km to Wh/mi and kWh/100 km", () => {
    expect(toConsumption(0.2, "imperial")).toBeCloseTo(321.8688, 4);
    expect(toConsumption(0.198, "metric")).toBeCloseTo(19.8, 6);
    expect(consumptionUnit("imperial")).toBe("Wh/mi");
    expect(consumptionUnit("metric")).toBe("kWh/100 km");
  });

  it("formats with the unit and keeps null as null", () => {
    expect(formatConsumption(0.2, "imperial")).toBe("322 Wh/mi");
    expect(formatConsumption(0.198, "metric")).toBe("19.8 kWh/100 km");
    expect(formatConsumption(null, "metric")).toBeNull();
  });

  it("formats distance in the user's units", () => {
    expect(formatDistanceValue(183.6, "metric")).toBe("184 km");
    expect(formatDistanceValue(16.09344, "imperial")).toBe("10 mi");
    expect(formatDistanceValue(null, "imperial")).toBeNull();
  });

  it("signs energy errors", () => {
    expect(formatSignedKwh(-0.51)).toBe("−0.51 kWh");
    expect(formatSignedKwh(0.485)).toBe("+0.49 kWh");
    expect(formatSignedKwh(0.001)).toBe("0.00 kWh");
    expect(formatSignedKwh(null)).toBeNull();
  });
});

describe("labels and links", () => {
  it("names known quality reasons and keeps unknown codes", () => {
    expect(qualityReasonLabel("start_time_bounded")).toBe(
      "Exact start time unknown",
    );
    expect(qualityReasonLabel("new_reason")).toBe("new_reason");
  });

  it("sends needs-review links to the matched trip, else to saved trips", () => {
    expect(reviewHref({ planned_trip_id: "abc" })).toBe("/trips/abc");
    expect(reviewHref({ planned_trip_id: null })).toBe("/trips/saved");
  });
});

describe("analytics URL params", () => {
  const VEHICLE = "0b9a6c4e-1f2d-4a5b-8c7d-6e5f4a3b2c1d";

  it("parses valid params", () => {
    expect(
      parseAnalyticsParams({
        vehicle: VEHICLE,
        from: "2026-11-01",
        to: "2026-11-30",
        group_by: "day",
        simulated: "1",
        tz: "America/New_York",
        cursor: "abc_DEF-1=",
      }),
    ).toEqual({
      vehicleId: VEHICLE,
      from: "2026-11-01",
      to: "2026-11-30",
      groupBy: "day",
      includeSimulated: true,
      tz: "America/New_York",
      cursor: "abc_DEF-1=",
    });
  });

  it("drops invalid values instead of passing them on", () => {
    expect(
      parseAnalyticsParams({
        vehicle: "not-a-uuid",
        from: "2026-02-30",
        to: "11/30/2026",
        group_by: "year",
        simulated: "true",
        tz: "../etc/passwd",
        cursor: "has space",
      }),
    ).toEqual({
      vehicleId: null,
      from: null,
      to: null,
      groupBy: "week",
      includeSimulated: false,
      tz: null,
      cursor: null,
    });
  });

  it("round-trips through the query string, leaving defaults out", () => {
    const parsed = parseAnalyticsParams({
      vehicle: VEHICLE,
      group_by: "week",
      tz: "UTC",
    });
    const search = analyticsSearch(parsed);
    expect(search).toBe(`?vehicle=${VEHICLE}&tz=UTC`);
    expect(
      parseAnalyticsParams(Object.fromEntries(new URLSearchParams(search))),
    ).toEqual(parsed);
    expect(analyticsSearch({})).toBe("");
  });

  it("builds the API query with UTC as the fallback zone", () => {
    expect(apiQuery(parseAnalyticsParams({}), VEHICLE)).toEqual({
      vehicle_id: VEHICLE,
      tz: "UTC",
      include_simulated: false,
    });
  });
});

describe("API errors", () => {
  it("reads details.reason from the error envelope", () => {
    expect(
      errorReason({
        error: {
          code: "VALIDATION_ERROR",
          details: { reason: "RANGE_TOO_LARGE" },
        },
      }),
    ).toBe("RANGE_TOO_LARGE");
    expect(errorReason({ detail: "x" })).toBeNull();
    expect(errorReason(null)).toBeNull();
  });

  it("explains range errors", () => {
    expect(rangeErrorMessage("RANGE_TOO_LARGE")).toMatch(/366/);
    expect(rangeErrorMessage(null)).toBe("Those filters aren't valid.");
  });
});
