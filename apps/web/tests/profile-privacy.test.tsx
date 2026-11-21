import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DeleteAccount } from "@/components/profile/delete-account";
import { formatRetention, type PrivacySettings } from "@/components/profile/privacy";
import { PrivacyPanel } from "@/components/profile/privacy-panel";

const deleteAccount = vi.fn();
const recordPrivacyConsent = vi.fn();
vi.mock("@/components/profile/privacy-actions", () => ({
  deleteAccount: (c: string) => deleteAccount(c),
  recordPrivacyConsent: (v: string, g: boolean) => recordPrivacyConsent(v, g),
}));

function privacy(overrides: Partial<PrivacySettings["consents"][number]> = {}): PrivacySettings {
  return {
    consents: [
      {
        kind: "privacy_notice",
        current_version: "2026-11-19",
        granted: null,
        version: null,
        recorded_at: null,
        is_current: false,
        ...overrides,
      },
    ],
    retention: { telemetry_days: 730, unsaved_plan_days: 30, refresh_log_days: 30 },
  };
}

describe("DeleteAccount", () => {
  beforeEach(() => deleteAccount.mockReset());

  function open() {
    render(<DeleteAccount />);
    fireEvent.click(screen.getByRole("button", { name: "Delete account" }));
    const dialog = screen.getByRole("alertdialog");
    const confirm = screen
      .getAllByRole("button", { name: "Delete account" })
      .find((b) => b.closest("[role=alertdialog]"))!;
    return { dialog, confirm, input: screen.getByLabelText("Type DELETE to confirm") };
  }

  it("keeps the confirm button disabled until DELETE is typed", () => {
    const { dialog, confirm, input } = open();
    expect(dialog).toHaveTextContent("Delete your account?");
    expect(confirm).toBeDisabled();
    fireEvent.change(input, { target: { value: "delete" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(input, { target: { value: "DELETE" } });
    expect(confirm).toBeEnabled();
    expect(deleteAccount).not.toHaveBeenCalled();
  });

  it("cancelling closes the dialog and forgets the typed text", async () => {
    const { input } = open();
    fireEvent.change(input, { target: { value: "DELETE" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Delete account" }));
    expect(screen.getByLabelText("Type DELETE to confirm")).toHaveValue("");
  });

  it("calls the action and shows its error in the dialog", async () => {
    deleteAccount.mockResolvedValue({ error: "Couldn't delete your account. Please try again." });
    const { confirm, input } = open();
    fireEvent.change(input, { target: { value: "DELETE" } });
    fireEvent.click(confirm);
    expect(deleteAccount).toHaveBeenCalledWith("DELETE");
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Couldn't delete your account."),
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });
});

describe("PrivacyPanel", () => {
  beforeEach(() => recordPrivacyConsent.mockReset());

  it("shows the retention windows from the API", () => {
    render(<PrivacyPanel privacy={privacy()} />);
    const panel = screen.getByRole("region", { name: "Privacy" });
    expect(panel).toHaveTextContent("kept for 2 years");
    expect(panel).toHaveTextContent("deleted after 30 days");
  });

  it("asks for acceptance and records it", async () => {
    recordPrivacyConsent.mockResolvedValue({ granted: true });
    render(<PrivacyPanel privacy={privacy()} />);
    expect(screen.getByText("Not accepted")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(recordPrivacyConsent).toHaveBeenCalledWith("2026-11-19", true);
    await waitFor(() => expect(screen.getByText("Accepted")).toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "Withdraw" })).toBeEnabled();
  });

  it("asks again when the notice version changed", () => {
    render(
      <PrivacyPanel
        privacy={privacy({
          granted: true,
          version: "2026-01-01",
          recorded_at: "2026-01-02T00:00:00Z",
          is_current: false,
        })}
      />,
    );
    expect(screen.getByText(/The notice changed since you accepted version 2026-01-01/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
  });

  it("shows a save error", async () => {
    recordPrivacyConsent.mockResolvedValue({ error: "Couldn't save your answer. Please try again." });
    render(<PrivacyPanel privacy={privacy({ granted: true, version: "2026-11-19", recorded_at: "2026-11-19T00:00:00Z", is_current: true })} />);
    fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(recordPrivacyConsent).toHaveBeenCalledWith("2026-11-19", false);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Couldn't save"));
  });
});

describe("formatRetention", () => {
  it.each([
    [730, "2 years"],
    [365, "1 year"],
    [30, "30 days"],
    [1, "1 day"],
    [400, "400 days"],
  ])("%i days → %s", (days, text) => {
    expect(formatRetention(days)).toBe(text);
  });
});
