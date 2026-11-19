import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ObservationReviewButtons } from "@/components/trips/observation-review-buttons";

import { LEG_1 } from "./observation-fixture";

const confirm = vi.fn();
const reject = vi.fn();
vi.mock("@/components/trips/observation-actions", () => ({
  confirmObservedTrip: (id: string) => confirm(id),
  rejectObservedTrip: (id: string) => reject(id),
}));

describe("ObservationReviewButtons", () => {
  beforeEach(() => {
    confirm.mockReset();
    reject.mockReset();
  });

  it("confirms, shows pending, then the new status", async () => {
    let resolve!: (v: unknown) => void;
    confirm.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<ObservationReviewButtons id={LEG_1} status="needs_review" label="drive" />);

    fireEvent.click(screen.getByRole("button", { name: "Confirm drive" }));
    expect(confirm).toHaveBeenCalledWith(LEG_1);
    expect(await screen.findByRole("button", { name: "Confirm drive" })).toHaveTextContent(
      "Confirming…",
    );
    expect(screen.getByRole("button", { name: "Reject drive" })).toBeDisabled();

    resolve({ status: "confirmed" });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Confirm drive" })).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Confirmed.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Reject drive" })).toBeEnabled();
  });

  it("rejects and offers to confirm again", async () => {
    reject.mockResolvedValue({ status: "rejected" });
    render(<ObservationReviewButtons id={LEG_1} status="auto" label="drive" />);
    fireEvent.click(screen.getByRole("button", { name: "Reject drive" }));
    await waitFor(() => expect(screen.getByText("Rejected.")).toBeVisible());
    expect(screen.queryByRole("button", { name: "Reject drive" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm drive" })).toBeEnabled();
  });

  it("shows the error and keeps both buttons", async () => {
    confirm.mockResolvedValue({ error: "Couldn't confirm this drive. Please try again." });
    render(<ObservationReviewButtons id={LEG_1} status="needs_review" label="drive" />);
    fireEvent.click(screen.getByRole("button", { name: "Confirm drive" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't confirm this drive");
    expect(screen.getByRole("button", { name: "Confirm drive" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reject drive" })).toBeEnabled();
  });

  it("hides the button for the status the drive already has", () => {
    render(<ObservationReviewButtons id={LEG_1} status="confirmed" label="drive" />);
    expect(screen.queryByRole("button", { name: "Confirm drive" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject drive" })).toBeVisible();
  });
});
