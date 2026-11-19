import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ObservationList } from "@/components/trips/observation-list";

import { LEG_2, makeObservedTrip, TRIP_ID } from "./observation-fixture";

vi.mock("@/components/trips/observation-actions", () => ({
  confirmObservedTrip: vi.fn(),
  rejectObservedTrip: vi.fn(),
}));

const HREFS = {
  all: "/trips/saved?view=observed",
  needs_review: "/trips/saved?view=observed&review=needs_review",
};

describe("ObservationList", () => {
  it("shows each drive with its bounded start, quality, reasons and review buttons", () => {
    render(
      <ObservationList
        items={[makeObservedTrip()]}
        units="metric"
        filter="all"
        filterHrefs={HREFS}
        nextHref={null}
        firstHref={null}
      />,
    );
    const row = screen.getByTestId("observed-drive");
    // Server render is UTC; a bounded start is a range, never one time.
    expect(within(row).getByTestId("drive-times")).toHaveTextContent(
      /started between Nov 9, \d\d:\d\d and Nov 10, \d\d:\d\d/,
    );
    expect(within(row).getByTestId("quality-badge")).toHaveTextContent("Low quality");
    const reasons = within(row).getByRole("list", { name: "Why this quality" });
    expect(within(reasons).getAllByRole("listitem")).toHaveLength(3);
    expect(reasons).toHaveTextContent("The exact start time isn't known");
    expect(within(row).getByTestId("review-status")).toHaveTextContent("Needs review");
    expect(within(row).getByTestId("drive-soc")).toHaveTextContent("66% → 61%");
    expect(within(row).getByTestId("drive-energy")).toHaveTextContent("3.5 ± 0.7 kWh used");
    expect(within(row).getByRole("button", { name: /^Confirm 29 km drive ending Nov 10 08:41 UTC$/ })).toBeEnabled();
    expect(within(row).getByRole("button", { name: /^Reject 29 km drive ending Nov 10 08:41 UTC$/ })).toBeEnabled();
  });

  it("marks the active filter and links both filters", () => {
    render(
      <ObservationList
        items={[]}
        units="metric"
        filter="needs_review"
        filterHrefs={HREFS}
        nextHref={null}
        firstHref={null}
      />,
    );
    const nav = screen.getByRole("navigation", { name: "Filter observed drives" });
    expect(within(nav).getByRole("link", { name: "Needs review" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "All drives" })).toHaveAttribute(
      "href",
      HREFS.all,
    );
    expect(screen.getByText("Nothing to review")).toBeVisible();
  });

  it("shows the empty state, missing energy, plan link, in-progress and pagination", () => {
    const { rerender } = render(
      <ObservationList
        items={[]}
        units="metric"
        filter="all"
        filterHrefs={HREFS}
        nextHref={null}
        firstHref={null}
      />,
    );
    expect(screen.getByText("No drives yet")).toBeVisible();

    rerender(
      <ObservationList
        items={[
          makeObservedTrip({
            energy_kwh: null,
            energy_kwh_uncertainty: null,
            energy_source: null,
            soc_end_percent: null,
            quality_reasons: ["soc_missing"],
            review_status: "auto",
            planned_trip_id: TRIP_ID,
            plan_leg_index: 2,
          }),
          makeObservedTrip({ id: LEG_2, in_progress: true, review_status: "auto" }),
        ]}
        units="imperial"
        filter="all"
        filterHrefs={HREFS}
        nextHref="/trips/saved?view=observed&cursor=abc"
        firstHref="/trips/saved?view=observed"
      />,
    );
    const [first, second] = screen.getAllByTestId("observed-drive");
    expect(within(first!).getByTestId("drive-energy")).toHaveTextContent("Energy not measured");
    expect(within(first!).getByTestId("drive-soc")).toHaveTextContent("66% → —");
    expect(within(first!).getByRole("link", { name: /Matched to a planned trip \(leg 2\)/ })).toHaveAttribute(
      "href",
      `/trips/${TRIP_ID}`,
    );
    expect(within(second!).getByText("In progress")).toBeVisible();
    expect(within(second!).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Older drives →" })).toHaveAttribute(
      "href",
      "/trips/saved?view=observed&cursor=abc",
    );
  });
});
