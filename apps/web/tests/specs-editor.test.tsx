import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpecsEditor } from "@/components/garage/specs-editor";

const updateVehicleSpecs = vi.fn();
vi.mock("@/app/(app)/garage/actions", () => ({
  updateVehicleSpecs: (id: string, body: unknown) => updateVehicleSpecs(id, body),
}));

const ID = "0b6c1f7e-2d3a-4c5b-8e9f-0a1b2c3d4e5f";
const specs = {
  usable_capacity_kwh: null,
  connectors: ["NACS"],
  charging_curve: [{ soc: 0.1, power_kw: 150 }],
};

function openForm() {
  render(<SpecsEditor vehicleId={ID} specs={specs} />);
  fireEvent.click(screen.getByRole("button", { name: "Edit specs" }));
  return screen.getByRole("form", { name: "Edit specs" });
}

describe("SpecsEditor", () => {
  beforeEach(() => updateVehicleSpecs.mockReset());

  it("prefills the form and saves the full body", async () => {
    updateVehicleSpecs.mockResolvedValue(undefined);
    const form = openForm();

    fireEvent.change(within(form).getByLabelText(/usable battery capacity/i), {
      target: { value: "68.5" },
    });
    fireEvent.click(within(form).getByLabelText("CCS1"));
    fireEvent.click(within(form).getByRole("button", { name: "Add point" }));
    fireEvent.change(within(form).getByLabelText("Row 2 battery level (%)"), {
      target: { value: "80" },
    });
    fireEvent.change(within(form).getByLabelText("Row 2 max power (kW)"), {
      target: { value: "60" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save specs" }));

    await waitFor(() =>
      expect(updateVehicleSpecs).toHaveBeenCalledWith(ID, {
        usable_capacity_kwh: 68.5,
        connectors: ["NACS", "CCS1"],
        charging_curve: [
          { soc: 0.1, power_kw: 150 },
          { soc: 0.8, power_kw: 60 },
        ],
      }),
    );
    // Back to the read-only view after a successful save.
    await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
  });

  it("shows validation errors without calling the server", () => {
    const form = openForm();
    fireEvent.change(within(form).getByLabelText(/usable battery capacity/i), {
      target: { value: "-1" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save specs" }));

    expect(within(form).getByRole("alert")).toHaveTextContent(/more than 0/);
    expect(updateVehicleSpecs).not.toHaveBeenCalled();
  });

  it("keeps the form open and shows the server error", async () => {
    updateVehicleSpecs.mockResolvedValue({ error: "Couldn't save the specs. Please try again." });
    const form = openForm();
    fireEvent.click(within(form).getByRole("button", { name: "Save specs" }));

    await waitFor(() =>
      expect(within(form).getByRole("alert")).toHaveTextContent("Couldn't save the specs."),
    );
    expect(screen.getByRole("form", { name: "Edit specs" })).toBeInTheDocument();
  });

  it("removes a curve row and cancels back to the view", () => {
    const form = openForm();
    fireEvent.click(within(form).getByRole("button", { name: "Remove row 1" }));
    expect(within(form).queryByLabelText("Row 1 battery level (%)")).not.toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Edit specs" })).toBeInTheDocument();
  });
});
