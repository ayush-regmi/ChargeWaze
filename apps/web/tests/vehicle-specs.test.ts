import { describe, expect, it } from "vitest";

import {
  specsToFormValues,
  validateSpecsForm,
  type SpecsFormValues,
} from "@/lib/vehicle-specs";

const valid: SpecsFormValues = {
  capacityKwh: "71.4",
  connectors: ["NACS", "CCS1"],
  curve: [
    { socPercent: "10", powerKw: "150" },
    { socPercent: "80", powerKw: "60" },
  ],
};

describe("specsToFormValues", () => {
  it("turns API specs into form strings with percentages", () => {
    expect(
      specsToFormValues({
        usable_capacity_kwh: 71.4,
        connectors: ["NACS", "Type2", "J1772"],
        charging_curve: [{ soc: 0.29, power_kw: 150 }],
      }),
    ).toEqual({
      capacityKwh: "71.4",
      // Unknown connector strings are dropped (they can't be edited back).
      connectors: ["NACS", "J1772"],
      curve: [{ socPercent: "29", powerKw: "150" }],
    });
  });

  it("handles missing specs", () => {
    expect(specsToFormValues(null)).toEqual({ capacityKwh: "", connectors: [], curve: [] });
  });
});

describe("validateSpecsForm", () => {
  it("builds the full PATCH body", () => {
    expect(validateSpecsForm(valid)).toEqual({
      ok: true,
      body: {
        usable_capacity_kwh: 71.4,
        connectors: ["NACS", "CCS1"],
        charging_curve: [
          { soc: 0.1, power_kw: 150 },
          { soc: 0.8, power_kw: 60 },
        ],
      },
    });
  });

  it("sends null capacity and empty lists when cleared", () => {
    expect(validateSpecsForm({ capacityKwh: " ", connectors: [], curve: [] })).toEqual({
      ok: true,
      body: { usable_capacity_kwh: null, connectors: [], charging_curve: [] },
    });
  });

  it.each([
    ["abc", "Enter a number."],
    ["0", "Must be more than 0 and at most 300 kWh."],
    ["300.5", "Must be more than 0 and at most 300 kWh."],
    ["70.123", "Use at most two decimal places."],
  ])("rejects capacity %s", (capacityKwh, message) => {
    const result = validateSpecsForm({ ...valid, capacityKwh });
    expect(result).toEqual({ ok: false, errors: { capacityKwh: message } });
  });

  it("rejects duplicate connectors", () => {
    const result = validateSpecsForm({ ...valid, connectors: ["NACS", "NACS"] });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.connectors).toMatch(/only be listed once/);
  });

  it.each([
    [{ socPercent: "", powerKw: "50" }, "Fill in both values."],
    [{ socPercent: "x", powerKw: "50" }, "Enter numbers."],
    [{ socPercent: "101", powerKw: "50" }, "Battery level must be 0–100%."],
    [{ socPercent: "50", powerKw: "0" }, "Power must be more than 0 and at most 1000 kW."],
    [{ socPercent: "50", powerKw: "1001" }, "Power must be more than 0 and at most 1000 kW."],
  ])("flags a bad curve row %o", (row, message) => {
    const result = validateSpecsForm({ ...valid, curve: [valid.curve[0], row] });
    expect(result).toEqual({ ok: false, errors: { curveRows: [undefined, message] } });
  });

  it("requires battery levels to increase", () => {
    const result = validateSpecsForm({ ...valid, curve: [...valid.curve].reverse() });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.curve).toMatch(/must go up/);
  });

  it("caps the number of curve points", () => {
    const curve = Array.from({ length: 21 }, (_, i) => ({
      socPercent: String(i * 4),
      powerKw: "50",
    }));
    const result = validateSpecsForm({ ...valid, curve });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.curve).toBe("At most 20 points.");
  });
});
