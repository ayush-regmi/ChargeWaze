import { describe, expect, it } from "vitest";

import {
  displayToKwhPerKm,
  formatConsumption,
  kwhPerKmToDisplay,
  specsToFormValues,
  validateSpecsForm,
  type SpecsFormValues,
} from "@/lib/vehicle-specs";

const NO_BASELINE = {
  baseline_kwh_per_km: null,
  catalog_baseline_kwh_per_km: null,
  baseline_source: null,
} as const;

const valid: SpecsFormValues = {
  capacityKwh: "71.4",
  consumption: "",
  connectors: ["NACS", "CCS1"],
  curve: [
    { socPercent: "10", powerKw: "150" },
    { socPercent: "80", powerKw: "60" },
  ],
};

describe("specsToFormValues", () => {
  it("turns API specs into form strings with percentages", () => {
    expect(
      specsToFormValues(
        {
          usable_capacity_kwh: 71.4,
          connectors: ["NACS", "Type2", "J1772"],
          charging_curve: [{ soc: 0.29, power_kw: 150 }],
          ...NO_BASELINE,
        },
        "imperial",
      ),
    ).toEqual({
      capacityKwh: "71.4",
      consumption: "",
      // Unknown connector strings are dropped (they can't be edited back).
      connectors: ["NACS", "J1772"],
      curve: [{ socPercent: "29", powerKw: "150" }],
    });
  });

  it("handles missing specs", () => {
    expect(specsToFormValues(null, "metric")).toEqual({
      capacityKwh: "",
      consumption: "",
      connectors: [],
      curve: [],
    });
  });
});

describe("validateSpecsForm", () => {
  it("builds the full PATCH body", () => {
    expect(validateSpecsForm(valid, "imperial")).toEqual({
      ok: true,
      body: {
        usable_capacity_kwh: 71.4,
        connectors: ["NACS", "CCS1"],
        charging_curve: [
          { soc: 0.1, power_kw: 150 },
          { soc: 0.8, power_kw: 60 },
        ],
        baseline_kwh_per_km: null,
      },
    });
  });

  it("sends null capacity and empty lists when cleared", () => {
    expect(
      validateSpecsForm(
        { capacityKwh: " ", consumption: " ", connectors: [], curve: [] },
        "imperial",
      ),
    ).toEqual({
      ok: true,
      body: {
        usable_capacity_kwh: null,
        connectors: [],
        charging_curve: [],
        baseline_kwh_per_km: null,
      },
    });
  });

  it.each([
    ["abc", "Enter a number."],
    ["0", "Must be more than 0 and at most 300 kWh."],
    ["300.5", "Must be more than 0 and at most 300 kWh."],
    ["70.123", "Use at most two decimal places."],
  ])("rejects capacity %s", (capacityKwh, message) => {
    const result = validateSpecsForm({ ...valid, capacityKwh }, "imperial");
    expect(result).toEqual({ ok: false, errors: { capacityKwh: message } });
  });

  it("rejects duplicate connectors", () => {
    const result = validateSpecsForm(
      { ...valid, connectors: ["NACS", "NACS"] },
      "imperial",
    );
    expect(result.ok).toBe(false);
    if (!result.ok)
      expect(result.errors.connectors).toMatch(/only be listed once/);
  });

  it.each([
    [{ socPercent: "", powerKw: "50" }, "Fill in both values."],
    [{ socPercent: "x", powerKw: "50" }, "Enter numbers."],
    [{ socPercent: "101", powerKw: "50" }, "Battery level must be 0–100%."],
    [
      { socPercent: "50", powerKw: "0" },
      "Power must be more than 0 and at most 1000 kW.",
    ],
    [
      { socPercent: "50", powerKw: "1001" },
      "Power must be more than 0 and at most 1000 kW.",
    ],
  ])("flags a bad curve row %o", (row, message) => {
    const result = validateSpecsForm(
      { ...valid, curve: [valid.curve[0], row] },
      "imperial",
    );
    expect(result).toEqual({
      ok: false,
      errors: { curveRows: [undefined, message] },
    });
  });

  it("requires battery levels to increase", () => {
    const result = validateSpecsForm(
      { ...valid, curve: [...valid.curve].reverse() },
      "imperial",
    );
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.curve).toMatch(/must go up/);
  });

  it("caps the number of curve points", () => {
    const curve = Array.from({ length: 21 }, (_, i) => ({
      socPercent: String(i * 4),
      powerKw: "50",
    }));
    const result = validateSpecsForm({ ...valid, curve }, "imperial");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.curve).toBe("At most 20 points.");
  });
});

describe("consumption units (phase4_plan F8)", () => {
  it("shows kWh/km as Wh/mi for imperial and kWh/100 km for metric", () => {
    expect(kwhPerKmToDisplay(0.2, "imperial")).toBe(322);
    expect(kwhPerKmToDisplay(0.2, "metric")).toBe(20);
    expect(kwhPerKmToDisplay(0.185, "metric")).toBe(18.5);
    expect(formatConsumption(0.2, "imperial")).toBe("322 Wh/mi");
    expect(formatConsumption(0.185, "metric")).toBe("18.5 kWh/100 km");
    expect(formatConsumption(null, "metric")).toBeNull();
  });

  it("converts back to kWh/km with 3 decimals", () => {
    expect(displayToKwhPerKm(322, "imperial")).toBe(0.2);
    expect(displayToKwhPerKm(300, "imperial")).toBe(0.186);
    expect(displayToKwhPerKm(18.5, "metric")).toBe(0.185);
    expect(displayToKwhPerKm(18.57, "metric")).toBe(0.186);
  });

  it.each([0.001, 0.15, 0.185, 0.2, 0.333, 0.5, 1])(
    "round-trips %s kWh/km through both unit systems",
    (kwhPerKm) => {
      for (const units of ["imperial", "metric"] as const) {
        expect(
          displayToKwhPerKm(kwhPerKmToDisplay(kwhPerKm, units), units),
        ).toBe(kwhPerKm);
      }
    },
  );

  it("prefills the user's value in their units", () => {
    const specs = {
      usable_capacity_kwh: 71.4,
      connectors: [],
      charging_curve: [],
      baseline_kwh_per_km: 0.185,
      catalog_baseline_kwh_per_km: 0.2,
      baseline_source: "vehicle" as const,
    };
    expect(specsToFormValues(specs, "imperial").consumption).toBe("298");
    expect(specsToFormValues(specs, "metric").consumption).toBe("18.5");
  });

  it("sends imperial input as kWh/km", () => {
    const result = validateSpecsForm(
      { ...valid, consumption: "300" },
      "imperial",
    );
    expect(result.ok && result.body.baseline_kwh_per_km).toBe(0.186);
  });

  it("sends metric input as kWh/km", () => {
    const result = validateSpecsForm(
      { ...valid, consumption: "18.5" },
      "metric",
    );
    expect(result.ok && result.body.baseline_kwh_per_km).toBe(0.185);
  });

  it.each([
    ["imperial", "abc", "Enter a number."],
    ["imperial", "0", "Must be more than 0 and at most 1609 Wh/mi."],
    ["imperial", "0.5", "Must be more than 0 and at most 1609 Wh/mi."],
    ["imperial", "1700", "Must be more than 0 and at most 1609 Wh/mi."],
    ["metric", "-3", "Must be more than 0 and at most 100 kWh/100 km."],
    ["metric", "120", "Must be more than 0 and at most 100 kWh/100 km."],
  ] as const)("%s rejects %s", (units, consumption, message) => {
    const result = validateSpecsForm({ ...valid, consumption }, units);
    expect(result).toEqual({ ok: false, errors: { consumption: message } });
  });
});
