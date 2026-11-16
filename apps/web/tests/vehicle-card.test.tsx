import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { formatUtc } from "@/components/garage/local-time";
import { SpecsPanel } from "@/components/garage/specs-panel";
import { EmptyGarage, VehicleCard } from "@/components/garage/vehicle-card";
import type { VehicleSummary } from "@/lib/vehicles";

// The server action module pulls in Supabase/Next server code; the card only
// needs a callable.
vi.mock("@/app/(app)/garage/actions", () => ({
  disconnectVehicle: vi.fn(),
  setPrimaryVehicle: vi.fn(),
}));

const base: VehicleSummary = {
  id: "0b6c1f7e-2d3a-4c5b-8e9f-0a1b2c3d4e5f",
  make: "Toyota",
  model: "bZ XLE",
  year: 2026,
  connection_status: "connected",
  supported_signals: ["soc", "range", "odometer", "charging_status"],
  last_state_recorded_at: "2026-11-06T18:52:00Z",
  is_stale: false,
};

describe("VehicleCard", () => {
  it("renders title, link, signals and the up-to-date marker", () => {
    render(<VehicleCard vehicle={base} />);
    const card = screen.getByRole("article", { name: "2026 Toyota bZ XLE" });
    expect(within(card).getByRole("link", { name: "2026 Toyota bZ XLE" })).toHaveAttribute(
      "href",
      `/garage/${base.id}`,
    );
    const signals = within(card).getByRole("list", { name: "Supported data" });
    expect(within(signals).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Battery level",
      "Range",
      "Odometer",
      "Charging",
    ]);
    expect(within(card).getByText("Up to date")).toBeInTheDocument();
    expect(card.querySelector("time")).toHaveAttribute("datetime", base.last_state_recorded_at);
    expect(within(card).getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  it("shows the stale marker", () => {
    render(<VehicleCard vehicle={{ ...base, is_stale: true }} />);
    expect(screen.getByText("Stale")).toHaveAttribute("data-freshness", "stale");
  });

  it("shows 'Awaiting first update' with no telemetry and no signals", () => {
    render(
      <VehicleCard
        vehicle={{ ...base, last_state_recorded_at: null, is_stale: true, supported_signals: [] }}
      />,
    );
    expect(screen.getByText("Awaiting first update")).toBeInTheDocument();
    expect(screen.queryByText(/last update/i)).not.toBeInTheDocument();
    expect(screen.getByText(/supported data will appear/i)).toBeInTheDocument();
  });

  it("has no disconnect button for a disconnected vehicle", () => {
    render(<VehicleCard vehicle={{ ...base, connection_status: "disconnected" }} />);
    expect(screen.queryByRole("button", { name: "Disconnect" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set as primary" })).not.toBeInTheDocument();
  });

  it("offers 'Set as primary' on a connected non-primary vehicle", () => {
    render(<VehicleCard vehicle={base} />);
    expect(screen.getByRole("button", { name: "Set as primary" })).toBeInTheDocument();
    expect(screen.queryByText("Primary")).not.toBeInTheDocument();
  });

  it("shows the Primary badge instead of the button on the primary vehicle", () => {
    render(<VehicleCard vehicle={base} isPrimary />);
    expect(screen.getByText("Primary")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set as primary" })).not.toBeInTheDocument();
  });
});

describe("EmptyGarage", () => {
  it("prompts to add a vehicle", () => {
    render(<EmptyGarage />);
    expect(screen.getByText("No vehicles yet")).toBeInTheDocument();
    expect(screen.getByText("Add vehicle")).toBeInTheDocument();
  });
});

describe("SpecsPanel", () => {
  it("renders specs", () => {
    render(
      <SpecsPanel
        specs={{
          usable_capacity_kwh: 71.4,
          connectors: ["NACS", "J1772"],
          charging_curve: [{ soc: 0.1, power_kw: 150 }],
        }}
      />,
    );
    expect(screen.getByText("71.4 kWh")).toBeInTheDocument();
    expect(screen.getByText("NACS, J1772")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("150 kW")).toBeInTheDocument();
  });

  it("says 'Not set' when capacity is unknown", () => {
    render(<SpecsPanel specs={{ usable_capacity_kwh: null, connectors: [], charging_curve: [] }} />);
    expect(screen.getAllByText("Not set")).toHaveLength(3);
    expect(screen.getByText(/doesn.t report battery capacity/i)).toBeInTheDocument();
  });

  it("handles missing specs", () => {
    render(<SpecsPanel specs={null} />);
    expect(screen.getAllByText("Not set")).toHaveLength(3);
  });
});

describe("formatUtc", () => {
  it("formats ISO timestamps and passes through garbage", () => {
    expect(formatUtc("2026-11-06T18:52:00Z")).toBe("2026-11-06 18:52 UTC");
    expect(formatUtc("nope")).toBe("nope");
  });
});
