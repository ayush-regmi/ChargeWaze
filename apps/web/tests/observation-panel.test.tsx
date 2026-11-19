import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { parseObservation } from "@/components/trips/observation-format";
import { ObservationPanel } from "@/components/trips/observation-panel";
import { parseStoredPlan } from "@/components/trips/plan-payload";

import { examplePlan, exampleObservation } from "./observation-fixture";

vi.mock("@/components/trips/observation-actions", () => ({
  confirmObservedTrip: vi.fn(),
  rejectObservedTrip: vi.fn(),
}));

function renderPanel(raw: Record<string, unknown>, plan = examplePlan()) {
  return render(
    <ObservationPanel
      observation={parseObservation(raw)!}
      plan={parseStoredPlan(plan)}
      units="metric"
    />,
  );
}

describe("ObservationPanel", () => {
  it("compares the plan with the matched drives (§5 example 3)", () => {
    renderPanel(exampleObservation());
    const panel = screen.getByTestId("observation-panel");
    expect(within(panel).getByRole("heading", { name: "Planned vs actual" })).toBeVisible();
    expect(within(panel).getByText("Completed")).toBeVisible();

    expect(screen.getByTestId("obs-distance")).toHaveTextContent("403 km406 km+3.4 km");
    expect(screen.getByTestId("obs-energy")).toHaveTextContent("80.5 kWh80.2 kWh−0.29 kWh");
    expect(screen.getByTestId("obs-arrival-soc")).toHaveTextContent("15%15%−0.3 pts");
    expect(screen.queryByTestId("energy-missing-note")).not.toBeInTheDocument();

    const legs = screen.getAllByTestId("observation-leg");
    expect(legs).toHaveLength(2);
    expect(within(legs[0]!).getByTestId("leg-soc")).toHaveTextContent(
      "Charge 80% → 17% planned · 80% → 18% actual (arrival +0.7 pts)",
    );
    expect(within(legs[1]!).getByTestId("leg-soc")).toHaveTextContent(
      "Charge 66% → 15% planned · 66% → 15% actual",
    );
    expect(within(legs[0]!).getByTestId("quality-badge")).toHaveTextContent("High quality");
    expect(screen.queryByTestId("legs-unpaired")).not.toBeInTheDocument();
  });

  it("says energy wasn't measured when a leg has no energy, without an error", () => {
    const raw = exampleObservation();
    const legs = raw.legs as Record<string, unknown>[];
    legs[1]!.energy_kwh = null;
    legs[1]!.soc_end_percent = null;
    legs[1]!.quality_flag = "low";
    legs[1]!.review_status = "needs_review";
    Object.assign(raw, {
      observed_energy_kwh: null,
      error_kwh: null,
      observed_arrival_soc: null,
      arrival_soc_error_points: null,
    });
    renderPanel(raw);

    expect(screen.getByTestId("obs-energy")).toHaveTextContent("80.5 kWhNot measured—");
    expect(screen.getByTestId("obs-arrival-soc")).toHaveTextContent("15%Not measured—");
    expect(screen.getByTestId("energy-missing-note")).toBeVisible();
    const leg2 = screen.getAllByTestId("observation-leg")[1]!;
    expect(leg2).toHaveTextContent("Energy not measured");
    expect(within(leg2).getByTestId("quality-badge")).toHaveTextContent("Low quality");
    expect(screen.getByRole("link", { name: "Some of these drives need review →" })).toHaveAttribute(
      "href",
      "/trips/saved?view=observed&review=needs_review",
    );
  });

  it("lists the drives on their own when they don't line up with the planned legs", () => {
    const raw = exampleObservation();
    (raw.legs as unknown[]).pop();
    renderPanel(raw);
    expect(screen.getByTestId("legs-unpaired")).toBeVisible();
    const legs = screen.getAllByTestId("observation-leg");
    expect(legs).toHaveLength(1);
    expect(legs[0]).toHaveTextContent("Drive 1");
  });
});
