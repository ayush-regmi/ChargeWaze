import { describe, expect, it } from "vitest";

import { freshness, signalLabel, vehicleTitle } from "@/lib/vehicles";

describe("vehicle helpers", () => {
  it("labels known signals and falls back to the code", () => {
    expect(signalLabel("soc")).toBe("Battery level");
    expect(signalLabel("charging_status")).toBe("Charging");
    expect(signalLabel("brand_new_signal")).toBe("brand_new_signal");
    expect(signalLabel("__proto__")).toBe("__proto__");
    expect(signalLabel("toString")).toBe("toString");
  });

  it("formats the vehicle title", () => {
    expect(vehicleTitle({ make: "Toyota", model: "bZ XLE", year: 2026 })).toBe(
      "2026 Toyota bZ XLE",
    );
  });

  it("derives freshness", () => {
    expect(freshness({ last_state_recorded_at: null, is_stale: true })).toBe("awaiting");
    expect(freshness({ last_state_recorded_at: "2026-11-06T18:52:00Z", is_stale: true })).toBe(
      "stale",
    );
    expect(freshness({ last_state_recorded_at: "2026-11-06T18:52:00Z", is_stale: false })).toBe(
      "fresh",
    );
  });
});
