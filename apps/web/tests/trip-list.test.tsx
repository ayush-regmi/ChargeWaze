import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TripList } from "@/components/trips/trip-list";

import { makeListItem, TRIP_ID } from "./trip-fixture";

describe("TripList", () => {
  it("shows an empty state that links to the planner", () => {
    render(<TripList items={[]} units="imperial" nextHref={null} firstHref={null} />);
    expect(screen.getByText("No saved trips yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Plan a trip" })).toHaveAttribute("href", "/trips");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("lists trips with distance in the user's units, total time and stops", () => {
    render(
      <TripList items={[makeListItem()]} units="imperial" nextHref={null} firstHref={null} />,
    );
    const link = screen.getByRole("link", { name: /Frederick, MD → Toronto, ON/ });
    expect(link).toHaveAttribute("href", `/trips/${TRIP_ID}`);
    expect(link).toHaveTextContent("Feasible");
    expect(link).toHaveTextContent("465 mi · 9 h 43 min · 3 stops");
    expect(link.querySelector("time")).toHaveAttribute("dateTime", "2026-11-06T18:02:00Z");
    expect(link).not.toHaveTextContent("Completed");
  });

  it("marks a plan matched to a drive as Completed (phase5_plan F7)", () => {
    render(
      <TripList
        items={[makeListItem({ status: "completed" })]}
        units="imperial"
        nextHref={null}
        firstHref={null}
      />,
    );
    expect(screen.getByRole("link", { name: /Frederick/ })).toHaveTextContent("Completed");
  });

  it("shows infeasible trips with missing values as —", () => {
    render(
      <TripList
        items={[
          makeListItem({
            plan_status: "infeasible",
            distance_km: null,
            total_minutes: null,
            stop_count: 0,
            origin_label: null,
          }),
        ]}
        units="metric"
        nextHref={null}
        firstHref={null}
      />,
    );
    const link = screen.getByRole("link", { name: /Unnamed start → Toronto, ON/ });
    expect(link).toHaveTextContent("Infeasible");
    expect(link).toHaveTextContent("— · — · No charging stops");
  });

  it("links to the next and first pages", () => {
    render(
      <TripList
        items={[makeListItem()]}
        units="metric"
        nextHref="/trips/saved?cursor=abc"
        firstHref="/trips/saved"
      />,
    );
    const nav = screen.getByRole("navigation", { name: "Saved trips pages" });
    expect(within(nav).getByRole("link", { name: /Older trips/ })).toHaveAttribute(
      "href",
      "/trips/saved?cursor=abc",
    );
    expect(within(nav).getByRole("link", { name: /First page/ })).toHaveAttribute(
      "href",
      "/trips/saved",
    );
  });

  it("an empty later page isn't the empty state", () => {
    render(<TripList items={[]} units="metric" nextHref={null} firstHref="/trips/saved" />);
    expect(screen.queryByText("No saved trips yet")).not.toBeInTheDocument();
    expect(screen.getByText("No more saved trips.")).toBeInTheDocument();
  });
});
