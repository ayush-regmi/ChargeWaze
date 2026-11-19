import { describe, expect, it } from "vitest";

import {
  formatPoints,
  formatSignedKwh,
  formatStartWindow,
  pairLegs,
  parseObservation,
  qualityReasonText,
  QUALITY_REASON_TEXT,
} from "@/components/trips/observation-format";
import { parseStoredPlan } from "@/components/trips/plan-payload";

import { examplePlan, exampleObservation, LEG_1, LEG_2 } from "./observation-fixture";

const NOW = new Date("2026-11-19T12:00:00Z");

describe("formatStartWindow", () => {
  it("shows an exact start as one time", () => {
    expect(
      formatStartWindow(
        {
          start_time: "2026-11-08T08:02:00Z",
          start_time_latest: "2026-11-08T08:02:00Z",
          start_time_is_exact: true,
        },
        "UTC",
        NOW,
      ),
    ).toBe("08:02");
  });

  it("shows a bounded start on one day as a range", () => {
    expect(
      formatStartWindow(
        {
          start_time: "2026-11-10T07:12:00Z",
          start_time_latest: "2026-11-10T08:41:00Z",
          start_time_is_exact: false,
        },
        "UTC",
        NOW,
      ),
    ).toBe("between 07:12 and 08:41");
  });

  it("adds each end's date when the range crosses midnight (§3.18 example)", () => {
    expect(
      formatStartWindow(
        {
          start_time: "2026-11-09T18:12:00Z",
          start_time_latest: "2026-11-10T08:41:00Z",
          start_time_is_exact: false,
        },
        "UTC",
        NOW,
      ),
    ).toBe("between Nov 9, 18:12 and Nov 10, 08:41");
  });

  it("uses the given time zone, so day boundaries follow the viewer", () => {
    // 01:00Z–03:30Z is the evening before in New York.
    expect(
      formatStartWindow(
        {
          start_time: "2026-11-10T01:00:00Z",
          start_time_latest: "2026-11-10T03:30:00Z",
          start_time_is_exact: false,
        },
        "America/New_York",
        NOW,
      ),
    ).toBe("between 20:00 and 22:30");
  });

  it("never shows two different bounds as a single time, even if flagged exact", () => {
    expect(
      formatStartWindow(
        {
          start_time: "2026-11-10T07:12:00Z",
          start_time_latest: "2026-11-10T08:41:00Z",
          start_time_is_exact: true,
        },
        "UTC",
        NOW,
      ),
    ).toMatch(/^between /);
  });

  it("includes the year for a different year", () => {
    expect(
      formatStartWindow(
        {
          start_time: "2025-12-31T22:00:00Z",
          start_time_latest: "2026-01-01T01:00:00Z",
          start_time_is_exact: false,
        },
        "UTC",
        NOW,
      ),
    ).toBe("between Dec 31, 2025, 22:00 and Jan 1, 01:00");
  });
});

describe("quality reasons", () => {
  it("has text for every D8 code and falls back to the raw code", () => {
    for (const code of [
      "soc_missing",
      "soc_increased",
      "capacity_unknown",
      "soc_delta_small",
      "soc_reading_stale",
      "start_time_bounded",
      "odometer_regression",
    ]) {
      expect(QUALITY_REASON_TEXT[code]).toBeTruthy();
    }
    expect(qualityReasonText("new_code")).toBe("new_code");
  });
});

describe("signed numbers", () => {
  it("formats points and kWh with a real minus sign", () => {
    expect(formatPoints(-0.3)).toBe("−0.3 pts");
    expect(formatPoints(0.7000001)).toBe("+0.7 pts");
    expect(formatPoints(0.01)).toBe("0 pts");
    expect(formatPoints(null)).toBeNull();
    expect(formatSignedKwh(-0.29)).toBe("−0.29 kWh");
    expect(formatSignedKwh(1.5)).toBe("+1.50 kWh");
  });
});

describe("parseObservation", () => {
  it("reads the §3.18e example", () => {
    const o = parseObservation(exampleObservation())!;
    expect(o.legs.map((l) => l.id)).toEqual([LEG_1, LEG_2]);
    expect(o.error_kwh).toBe(-0.29);
    expect(o.legs[0]!.quality_flag).toBe("high");
  });

  it.each([null, undefined, "x", [], {}, { legs: [] }, { legs: [{ id: "x" }] }])(
    "treats %j as no observation",
    (raw) => {
      expect(parseObservation(raw)).toBeNull();
    },
  );

  it("keeps unknown enum values out instead of guessing", () => {
    const raw = exampleObservation();
    (raw.legs as Record<string, unknown>[])[0]!.quality_flag = "great";
    expect(parseObservation(raw)!.legs[0]!.quality_flag).toBeNull();
  });

  it("orders legs by plan_leg_index", () => {
    const raw = exampleObservation();
    (raw.legs as unknown[]).reverse();
    expect(parseObservation(raw)!.legs.map((l) => l.plan_leg_index)).toEqual([1, 2]);
  });
});

describe("pairLegs", () => {
  it("pairs planned leg n with observed leg n (§5 example 3)", () => {
    const pairs = pairLegs(parseStoredPlan(examplePlan()), parseObservation(exampleObservation())!)!;
    expect(pairs).toHaveLength(2);
    expect(pairs[0]!.planned).toEqual({
      index: 1,
      distance_km: 220,
      start_soc: 0.8,
      arrival_soc: 0.173,
    });
    // Planned arrival at B 0.173 vs observed 18 % → +0.7 points.
    expect(formatPoints(pairs[0]!.arrival_soc_error_points)).toBe("+0.7 pts");
    expect(pairs[1]!.planned).toEqual({
      index: 2,
      distance_km: 180,
      start_soc: 0.66,
      arrival_soc: 0.15296,
    });
  });

  it("returns null when the counts don't line up", () => {
    const raw = exampleObservation();
    (raw.legs as unknown[]).pop();
    expect(pairLegs(parseStoredPlan(examplePlan()), parseObservation(raw)!)).toBeNull();
    expect(pairLegs(null, parseObservation(exampleObservation())!)).toBeNull();
  });
});
