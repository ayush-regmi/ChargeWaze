import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SetPrimaryButton } from "@/components/garage/set-primary-button";

const setPrimaryVehicle = vi.fn();
vi.mock("@/app/(app)/garage/actions", () => ({
  setPrimaryVehicle: (id: string) => setPrimaryVehicle(id),
}));

const ID = "0b6c1f7e-2d3a-4c5b-8e9f-0a1b2c3d4e5f";

describe("SetPrimaryButton", () => {
  beforeEach(() => setPrimaryVehicle.mockReset());

  it("calls the action with the vehicle id", async () => {
    setPrimaryVehicle.mockResolvedValue(undefined);
    render(<SetPrimaryButton vehicleId={ID} />);
    fireEvent.click(screen.getByRole("button", { name: "Set as primary" }));
    await waitFor(() => expect(setPrimaryVehicle).toHaveBeenCalledWith(ID));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the error when the action fails", async () => {
    setPrimaryVehicle.mockResolvedValue({ error: "That vehicle couldn't be found." });
    render(<SetPrimaryButton vehicleId={ID} />);
    fireEvent.click(screen.getByRole("button", { name: "Set as primary" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("That vehicle couldn't be found."),
    );
  });
});
