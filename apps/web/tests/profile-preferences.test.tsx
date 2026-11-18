import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PreferencesEditor } from "@/components/profile/preferences-editor";
import {
  networkNameError,
  preferencesToFormValues,
  validatePreferencesForm,
  type Preferences,
  type PreferencesFormValues,
} from "@/components/profile/preferences";

const updatePreferences = vi.fn();
vi.mock("@/app/(app)/profile/actions", () => ({
  updatePreferences: (body: unknown) => updatePreferences(body),
}));

const prefs: Preferences = {
  units: "imperial",
  reserve_soc: 0.15,
  preferred_charge_to: 0.8,
  max_detour_minutes: 20,
  preferred_networks: ["Tesla Supercharger", "EVgo"],
  primary_vehicle_id: null,
};

function formValues(patch: Partial<PreferencesFormValues> = {}): PreferencesFormValues {
  return { ...preferencesToFormValues(prefs), ...patch };
}

function errorsFor(patch: Partial<PreferencesFormValues>) {
  const result = validatePreferencesForm(formValues(patch));
  if (result.ok) throw new Error("expected validation to fail");
  return result.errors;
}

describe("validatePreferencesForm", () => {
  it("round-trips stored preferences into the PATCH body", () => {
    expect(preferencesToFormValues(prefs)).toMatchObject({
      reservePercent: "15",
      chargeToPercent: "80",
      maxDetourMinutes: "20",
    });
    expect(validatePreferencesForm(formValues())).toEqual({
      ok: true,
      body: {
        units: "imperial",
        reserve_soc: 0.15,
        preferred_charge_to: 0.8,
        max_detour_minutes: 20,
        preferred_networks: ["Tesla Supercharger", "EVgo"],
      },
    });
  });

  it("converts percentages with one decimal to 3-decimal fractions", () => {
    const result = validatePreferencesForm(
      formValues({ reservePercent: "12.5", chargeToPercent: " 90.1 " }),
    );
    expect(result.ok && result.body.reserve_soc).toBe(0.125);
    expect(result.ok && result.body.preferred_charge_to).toBe(0.901);
  });

  it("shows 3-decimal fractions without float noise", () => {
    expect(preferencesToFormValues({ ...prefs, reserve_soc: 0.29 }).reservePercent).toBe("29");
    expect(preferencesToFormValues({ ...prefs, reserve_soc: 0.125 }).reservePercent).toBe("12.5");
  });

  it.each([
    ["", "Enter a percentage."],
    ["abc", "Enter a number."],
    ["-5", "Enter a number."],
    ["10.25", "Use at most one decimal place."],
    ["101", "Must be between 0 and 100%."],
  ])("rejects charge-to %j", (raw, message) => {
    expect(errorsFor({ chargeToPercent: raw }).chargeToPercent).toBe(message);
  });

  it("requires reserve below charge-to (DB/API ordering rule)", () => {
    expect(errorsFor({ reservePercent: "80" }).reservePercent).toMatch(/lower than/);
    expect(validatePreferencesForm(formValues({ reservePercent: "79.9" })).ok).toBe(true);
  });

  it.each([
    ["", "Enter a whole number of minutes."],
    ["1.5", "Enter a whole number of minutes."],
    ["-1", "Enter a whole number of minutes."],
    ["241", "Must be between 0 and 240 minutes."],
  ])("rejects detour %j", (raw, message) => {
    expect(errorsFor({ maxDetourMinutes: raw }).maxDetourMinutes).toBe(message);
  });

  it("accepts the detour bounds", () => {
    expect(validatePreferencesForm(formValues({ maxDetourMinutes: "0" })).ok).toBe(true);
    expect(validatePreferencesForm(formValues({ maxDetourMinutes: "240" })).ok).toBe(true);
  });

  it("rejects duplicate networks ignoring case and trims names", () => {
    expect(errorsFor({ networks: ["EVgo", " evgo "] }).networks).toMatch(/already in the list/);
    const result = validatePreferencesForm(formValues({ networks: ["  EVgo  "] }));
    expect(result.ok && result.body.preferred_networks).toEqual(["EVgo"]);
  });

  it("caps network count and name length", () => {
    const twenty = Array.from({ length: 20 }, (_, i) => `Net ${i}`);
    expect(validatePreferencesForm(formValues({ networks: twenty })).ok).toBe(true);
    expect(errorsFor({ networks: [...twenty, "One more"] }).networks).toBe("At most 20 networks.");
    expect(networkNameError([], "x".repeat(101))).toMatch(/at most 100/);
    expect(networkNameError([], "x".repeat(100))).toBeNull();
    expect(networkNameError([], "   ")).toBe("Enter a network name.");
  });
});

function openForm() {
  render(<PreferencesEditor preferences={prefs} />);
  fireEvent.click(screen.getByRole("button", { name: "Edit preferences" }));
  return screen.getByRole("form", { name: "Edit preferences" });
}

describe("PreferencesEditor", () => {
  beforeEach(() => updatePreferences.mockReset());

  it("shows the stored preferences read-only", () => {
    render(<PreferencesEditor preferences={prefs} />);
    const view = screen.getByRole("region", { name: "Preferences" });
    expect(within(view).getByText("Miles, °F")).toBeInTheDocument();
    expect(within(view).getByText("15%")).toBeInTheDocument();
    expect(within(view).getByText("80%")).toBeInTheDocument();
    expect(within(view).getByText("20 minutes")).toBeInTheDocument();
    expect(within(view).getByText("Tesla Supercharger, EVgo")).toBeInTheDocument();
  });

  it("saves the edited preferences and shows the stored result", async () => {
    updatePreferences.mockResolvedValue({
      preferences: {
        ...prefs,
        units: "metric",
        reserve_soc: 0.1,
        max_detour_minutes: 30,
        preferred_networks: ["EVgo", "Electrify America"],
      },
    });
    const form = openForm();

    fireEvent.click(within(form).getByLabelText("Kilometers, °C"));
    fireEvent.change(within(form).getByLabelText("Reserve at destination (%)"), {
      target: { value: "10" },
    });
    fireEvent.change(within(form).getByLabelText("Max charging detour (minutes)"), {
      target: { value: "30" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Remove Tesla Supercharger" }));
    fireEvent.change(within(form).getByLabelText("Network name"), {
      target: { value: " Electrify America " },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    fireEvent.click(within(form).getByRole("button", { name: "Save preferences" }));

    await waitFor(() =>
      expect(updatePreferences).toHaveBeenCalledWith({
        units: "metric",
        reserve_soc: 0.1,
        preferred_charge_to: 0.8,
        max_detour_minutes: 30,
        preferred_networks: ["EVgo", "Electrify America"],
      }),
    );
    await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("Preferences saved.");
    expect(screen.getByText("Kilometers, °C")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("EVgo, Electrify America")).toBeInTheDocument();
  });

  it("shows validation errors without calling the server", () => {
    const form = openForm();
    fireEvent.change(within(form).getByLabelText("Reserve at destination (%)"), {
      target: { value: "90" },
    });
    fireEvent.change(within(form).getByLabelText("Max charging detour (minutes)"), {
      target: { value: "500" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save preferences" }));

    const alerts = within(form).getAllByRole("alert").map((a) => a.textContent);
    expect(alerts).toEqual([
      "Reserve must be lower than the charge-to limit.",
      "Must be between 0 and 240 minutes.",
    ]);
    expect(within(form).getByLabelText("Reserve at destination (%)")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(updatePreferences).not.toHaveBeenCalled();
  });

  it("rejects a duplicate network when adding", () => {
    const form = openForm();
    fireEvent.change(within(form).getByLabelText("Network name"), { target: { value: "evgo" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("evgo is already in the list.");
    expect(within(form).getAllByRole("listitem")).toHaveLength(2);
  });

  it("keeps the form open with the server error when saving fails", async () => {
    updatePreferences.mockResolvedValue({
      error: "Couldn't save your preferences. Please try again.",
    });
    const form = openForm();
    fireEvent.click(within(form).getByRole("button", { name: "Save preferences" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Couldn't save your preferences. Please try again.",
    );
    // Checked synchronously on purpose: the error and the end of saving must
    // commit together, never an error beside a "Saving…" button.
    expect(screen.getByRole("form", { name: "Edit preferences" })).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Save preferences" })).toBeEnabled();
  });

  it("disables the buttons while saving", async () => {
    let resolve: (v: unknown) => void = () => {};
    updatePreferences.mockReturnValue(new Promise((r) => (resolve = r)));
    const form = openForm();
    fireEvent.click(within(form).getByRole("button", { name: "Save preferences" }));

    expect(await within(form).findByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(within(form).getByRole("button", { name: "Cancel" })).toBeDisabled();
    resolve({ preferences: prefs });
    await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
  });

  it("cancel discards edits", () => {
    const form = openForm();
    fireEvent.click(within(form).getByLabelText("Kilometers, °C"));
    fireEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Miles, °F")).toBeInTheDocument();
    expect(updatePreferences).not.toHaveBeenCalled();
  });
});
