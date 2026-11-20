import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RefreshButton } from "@/components/dashboard/refresh-button";

const refreshVehicle = vi.fn();
vi.mock("@/components/dashboard/refresh-actions", () => ({
  refreshVehicle: (id: string) => refreshVehicle(id),
}));

const ID = "0b6c1f7e-2d3a-4c5b-8e9f-0a1b2c3d4e5f";

describe("RefreshButton", () => {
  beforeEach(() => refreshVehicle.mockReset());

  it("shows progress while refreshing and nothing extra once data is fresh", async () => {
    let resolve: (value: unknown) => void = () => {};
    refreshVehicle.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<RefreshButton vehicleId={ID} />);

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled());
    expect(refreshVehicle).toHaveBeenCalledWith(ID);

    resolve({ ok: true, inserted: 4, isStale: false, recordedAt: "2026-11-19T15:00:00Z" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());
    expect(screen.queryByTestId("refresh-message")).not.toBeInTheDocument();
  });

  it("says so honestly when the car had nothing newer", async () => {
    refreshVehicle.mockResolvedValue({
      ok: true,
      inserted: 0,
      isStale: true,
      recordedAt: "2026-11-18T10:00:00Z",
    });
    render(<RefreshButton vehicleId={ID} />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    const message = await screen.findByRole("status");
    expect(message).toHaveTextContent(/Your car hasn.t reported anything newer\. Its last report was/);
    expect(message.querySelector("time")).toHaveAttribute("datetime", "2026-11-18T10:00:00Z");
  });

  it("handles a car that has never reported", async () => {
    refreshVehicle.mockResolvedValue({ ok: true, inserted: 0, isStale: true, recordedAt: null });
    render(<RefreshButton vehicleId={ID} />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Your car hasn't reported any data yet.",
    );
  });

  it("shows the error and lets the user try again", async () => {
    refreshVehicle.mockResolvedValueOnce({
      ok: false,
      error: "This car was refreshed recently. You can refresh again in 10 min.",
    });
    render(<RefreshButton vehicleId={ID} />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You can refresh again in 10 min.");

    refreshVehicle.mockResolvedValueOnce({ ok: true, inserted: 2, isStale: false, recordedAt: null });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});
