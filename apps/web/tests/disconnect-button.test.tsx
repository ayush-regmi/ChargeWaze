import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DisconnectButton } from "@/components/garage/disconnect-button";

const disconnectVehicle = vi.fn();
vi.mock("@/app/(app)/garage/actions", () => ({
  disconnectVehicle: (id: string) => disconnectVehicle(id),
}));

const ID = "0b6c1f7e-2d3a-4c5b-8e9f-0a1b2c3d4e5f";

describe("DisconnectButton", () => {
  beforeEach(() => disconnectVehicle.mockReset());

  it("asks for confirmation before disconnecting", async () => {
    render(<DisconnectButton vehicleId={ID} vehicleName="2026 Toyota bZ" />);

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Disconnect 2026 Toyota bZ?");
    expect(disconnectVehicle).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(disconnectVehicle).not.toHaveBeenCalled();
  });

  it("calls the action on confirm and shows an error if it fails", async () => {
    disconnectVehicle.mockResolvedValue({ error: "Couldn't disconnect the vehicle." });
    render(<DisconnectButton vehicleId={ID} vehicleName="2026 Toyota bZ" />);

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    const dialogConfirm = screen
      .getAllByRole("button", { name: "Disconnect" })
      .find((b) => b.closest("[role=alertdialog]"));
    fireEvent.click(dialogConfirm!);


    expect(disconnectVehicle).toHaveBeenCalledWith(ID);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Couldn't disconnect the vehicle."),
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });
});
