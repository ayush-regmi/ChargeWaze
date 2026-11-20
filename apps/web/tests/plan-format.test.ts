import { describe, expect, it } from "vitest";

import type { Preferences } from "@/components/profile/preferences";
import {
  changedPlanPreferences,
  parseSocPercent,
  planErrorOutcome,
  sliceRouteByKm,
  socPromptDefault,
  specFieldLabel,
  stopSocLabel,
} from "@/lib/plan-format";

/** Encode `[lat, lon]` points as a precision-6 polyline (test helper). */
function encode(points: [number, number][]): string {
  let out = "";
  let prev = [0, 0];
  const enc = (v: number) => {
    let n = v < 0 ? ~(v << 1) : v << 1;
    while (n >= 0x20) {
      out += String.fromCharCode((0x20 | (n & 0x1f)) + 63);
      n >>= 5;
    }
    out += String.fromCharCode(n + 63);
  };
  for (const [lat, lon] of points) {
    const cur = [Math.round(lat * 1e6), Math.round(lon * 1e6)];
    enc(cur[0] - prev[0]);
    enc(cur[1] - prev[1]);
    prev = cur;
  }
  return out;
}

const envelope = (code: string, details?: Record<string, unknown>) => ({
  error: { code, message: "x", details: details ?? null },
});

describe("planErrorOutcome", () => {
  it("reads STARTING_SOC_REQUIRED with its hint", () => {
    expect(
      planErrorOutcome(
        envelope("VALIDATION_ERROR", {
          reason: "STARTING_SOC_REQUIRED",
          last_soc: 0.64,
          last_recorded_at: "2026-11-07T10:01:00Z",
        }),
        422,
      ),
    ).toEqual({ kind: "soc_required", lastSoc: 0.64, lastRecordedAt: "2026-11-07T10:01:00Z" });
    expect(
      planErrorOutcome(
        envelope("VALIDATION_ERROR", { reason: "STARTING_SOC_REQUIRED", last_soc: null }),
        422,
      ),
    ).toEqual({ kind: "soc_required", lastSoc: null, lastRecordedAt: null });
  });

  it("ignores an out-of-range last_soc instead of prefilling it", () => {
    const out = planErrorOutcome(
      envelope("VALIDATION_ERROR", { reason: "STARTING_SOC_REQUIRED", last_soc: 64 }),
      422,
    );
    expect(out).toMatchObject({ kind: "soc_required", lastSoc: null });
  });

  it("reads specs, route index and preference-field errors", () => {
    expect(
      planErrorOutcome(
        envelope("VALIDATION_ERROR", {
          reason: "VEHICLE_SPECS_INCOMPLETE",
          missing: ["charging_curve", "baseline_kwh_per_km"],
        }),
        422,
      ),
    ).toEqual({ kind: "specs_incomplete", missing: ["charging_curve", "baseline_kwh_per_km"] });
    expect(
      planErrorOutcome(
        envelope("VALIDATION_ERROR", { reason: "ROUTE_INDEX_UNAVAILABLE", route_count: 1 }),
        422,
      ),
    ).toEqual({ kind: "route_unavailable", routeCount: 1 });
    expect(
      planErrorOutcome(
        envelope("VALIDATION_ERROR", { fields: ["reserve_soc"], max_charge_soc: 0.8 }),
        422,
      ),
    ).toEqual({ kind: "preferences_invalid", fields: ["reserve_soc"], maxChargeSoc: 0.8 });
  });

  it("maps provider errors, 401 and anything unknown", () => {
    expect(planErrorOutcome(envelope("STATIONS_UNREACHABLE"), 503)).toEqual({
      kind: "error",
      code: "STATIONS_UNREACHABLE",
    });
    expect(planErrorOutcome(envelope("MAPBOX_ERROR"), 502)).toEqual({
      kind: "error",
      code: "MAPBOX_ERROR",
    });
    expect(planErrorOutcome(envelope("VALIDATION_ERROR", { errors: [] }), 422)).toEqual({
      kind: "error",
      code: "VALIDATION_ERROR",
    });
    expect(planErrorOutcome({}, 401)).toEqual({ kind: "error", code: "UNAUTHENTICATED" });
    expect(planErrorOutcome("boom", 500)).toEqual({ kind: "error", code: "UNKNOWN" });
    expect(planErrorOutcome(envelope("SOMETHING_NEW"), 500)).toEqual({
      kind: "error",
      code: "UNKNOWN",
    });
  });

  it("reads the per-user plan limit with its retry hint", () => {
    expect(
      planErrorOutcome(envelope("RATE_LIMITED", { limit: "plan", retry_after_seconds: 95 }), 429),
    ).toEqual({ kind: "rate_limited", retryAfterSeconds: 95 });
    expect(planErrorOutcome(envelope("RATE_LIMITED"), 429)).toEqual({
      kind: "rate_limited",
      retryAfterSeconds: null,
    });
  });
});

describe("labels", () => {
  it("labels a stop's arrival → departure charge", () => {
    expect(stopSocLabel({ arrival_soc: 0.162, departure_soc: 0.61 })).toBe("16% → 61%");
    expect(stopSocLabel({ arrival_soc: null, departure_soc: 0.61 })).toBe("— → 61%");
  });

  it("names missing spec fields", () => {
    expect(specFieldLabel("charging_curve")).toBe("charging curve");
    expect(specFieldLabel("something_new")).toBe("something_new");
  });
});

describe("starting SoC", () => {
  it("parses whole percentages only", () => {
    expect(parseSocPercent(" 64 ")).toEqual({ value: 0.64 });
    expect(parseSocPercent("0")).toEqual({ value: 0 });
    expect(parseSocPercent("100")).toEqual({ value: 1 });
    expect(parseSocPercent("101")).toHaveProperty("error");
    expect(parseSocPercent("64.5")).toHaveProperty("error");
    expect(parseSocPercent("")).toHaveProperty("error");
    expect(parseSocPercent("-5")).toHaveProperty("error");
  });

  it("prefills the prompt from the last reading", () => {
    expect(socPromptDefault(0.637)).toBe("64");
    expect(socPromptDefault(null)).toBe("");
  });
});

describe("changedPlanPreferences", () => {
  const stored: Preferences = {
    units: "imperial",
    reserve_soc: 0.15,
    preferred_charge_to: 0.8,
    max_detour_minutes: 20,
    preferred_networks: ["EVgo"],
    primary_vehicle_id: null,
  };
  const body = {
    units: "imperial" as const,
    reserve_soc: 0.15,
    preferred_charge_to: 0.8,
    max_detour_minutes: 20,
    preferred_networks: ["EVgo"],
  };

  it("is null when nothing changed", () => {
    expect(changedPlanPreferences(body, stored)).toBeNull();
  });

  it("sends only the changed keys", () => {
    expect(changedPlanPreferences({ ...body, reserve_soc: 0.5, preferred_charge_to: 0.55 }, stored)).toEqual({
      reserve_soc: 0.5,
      preferred_charge_to: 0.55,
    });
    expect(changedPlanPreferences({ ...body, preferred_networks: [] }, stored)).toEqual({
      preferred_networks: [],
    });
    expect(changedPlanPreferences({ ...body, max_detour_minutes: 5 }, stored)).toEqual({
      max_detour_minutes: 5,
    });
  });
});

describe("sliceRouteByKm", () => {
  // Along the equator: 0°, 1°, 2° east, each ≈ 111.2 km apart. Mapbox says
  // the road is 200 km, so 100 road-km is the middle point.
  const line = encode([
    [0, 0],
    [0, 1],
    [0, 2],
  ]);

  it("cuts the stretch on the road-distance axis, interpolating its ends", () => {
    const pts = sliceRouteByKm(line, 200, 50, 150)!;
    expect(pts).toHaveLength(3);
    expect(pts[0].lat).toBeCloseTo(0, 6);
    expect(pts[0].lon).toBeCloseTo(0.5, 4);
    expect(pts[1]).toEqual({ lat: 0, lon: 1 });
    expect(pts[2].lon).toBeCloseTo(1.5, 4);
  });

  it("clamps to the route and rejects empty ranges or bad input", () => {
    const pts = sliceRouteByKm(line, 200, 150, 900)!;
    expect(pts[pts.length - 1].lon).toBeCloseTo(2, 6);
    expect(sliceRouteByKm(line, 200, 120, 120)).toBeNull();
    expect(sliceRouteByKm(line, 0, 0, 10)).toBeNull();
    expect(sliceRouteByKm("not a polyline!", 200, 0, 10)).toBeNull();
  });
});
