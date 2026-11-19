import { describe, expect, it } from "vitest";

import { formatMinutes, formatSoc, parseStoredPlan } from "@/components/trips/plan-payload";

import { feasiblePlan, infeasiblePlan, STATION_ID } from "./trip-fixture";

describe("parseStoredPlan", () => {
  it("reads the feasible §3.7 body unchanged", () => {
    const plan = parseStoredPlan(feasiblePlan())!;
    expect(plan.status).toBe("feasible");
    expect(plan.origin).toEqual({ lat: 39.4143, lon: -77.4105, label: "Frederick, MD" });
    expect(plan.route?.distance_km).toBe(748.1);
    expect(plan.stops).toHaveLength(1);
    expect(plan.stops[0].station.id).toBe(STATION_ID);
    expect(plan.stops[0].arrival_soc).toBe(0.162);
    expect(plan.totals?.total_minutes).toBe(583);
    expect(plan.assumptions?.charging_curve_source).toBe("catalog_derived");
    expect(plan.candidates?.excluded_unknown_power).toBe(3);
    expect(plan.reason).toBeNull();
  });

  it("reads the infeasible body with its reason and limiting segment", () => {
    const plan = parseStoredPlan(infeasiblePlan())!;
    expect(plan.status).toBe("infeasible");
    expect(plan.totals).toBeNull();
    expect(plan.arrival_soc).toBeNull();
    expect(plan.reason?.code).toBe("DESTINATION_UNREACHABLE");
    expect(plan.reason?.limiting_segment).toEqual({
      from_km: 412,
      to_km: 640,
      required_soc: 0.79,
      available_soc: 0.8,
    });
  });

  it("handles NO_DRIVING_ROUTE (route null)", () => {
    const plan = parseStoredPlan({
      ...infeasiblePlan(),
      route: null,
      reason: { code: "NO_DRIVING_ROUTE", message: "No route.", limiting_segment: null },
    })!;
    expect(plan.route).toBeNull();
    expect(plan.reason?.limiting_segment).toBeNull();
  });

  it("turns junk into nulls instead of guessing or throwing", () => {
    expect(parseStoredPlan(null)).toBeNull();
    expect(parseStoredPlan("x")).toBeNull();
    expect(parseStoredPlan([])).toBeNull();
    const plan = parseStoredPlan({
      status: "maybe",
      origin: { lat: 200, lon: 0 },
      stops: [null, { sequence: 2, station: "x", arrival_soc: "0.5" }, { sequence: 1 }],
      totals: "none",
      assumptions: { preferred_networks: ["EVgo", 3] },
    })!;
    expect(plan.status).toBeNull();
    expect(plan.origin).toBeNull();
    expect(plan.stops.map((s) => s.sequence)).toEqual([1, 2]);
    expect(plan.stops[1].arrival_soc).toBeNull();
    expect(plan.stops[1].station.name).toBeNull();
    expect(plan.totals).toBeNull();
    expect(plan.assumptions?.preferred_networks).toEqual(["EVgo"]);
    expect(plan.route).toBeNull();
  });
});

describe("formatters", () => {
  it("formats SoC fractions as whole percents", () => {
    expect(formatSoc(0.162)).toBe("16%");
    expect(formatSoc(1)).toBe("100%");
    expect(formatSoc(null)).toBeNull();
  });

  it("formats minutes", () => {
    expect(formatMinutes(0)).toBe("0 min");
    expect(formatMinutes(45)).toBe("45 min");
    expect(formatMinutes(60)).toBe("1 h");
    expect(formatMinutes(583)).toBe("9 h 43 min");
    expect(formatMinutes(null)).toBeNull();
    expect(formatMinutes(Number.NaN)).toBeNull();
  });
});
