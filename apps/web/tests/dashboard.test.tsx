import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatTimeAgo } from "@/components/dashboard/time-ago";
import { NoVehicle, VehicleStateView } from "@/components/dashboard/vehicle-state-view";
import { pickDashboardVehicle, type VehicleState, type VehicleSummary } from "@/lib/vehicles";

const vehicle = {
  id: "0b6c1f7e-2d3a-4c5b-8e9f-0a1b2c3d4e5f",
  make: "Toyota",
  model: "bZ XLE",
  year: 2026,
};

const fresh: VehicleState = {
  vehicle_id: vehicle.id,
  recorded_at: "2026-11-06T18:52:00Z",
  received_at: "2026-11-06T18:52:08Z",
  source: "smartcar",
  is_stale: false,
  signals: {
    soc_percent: 72,
    range_km: 296,
    odometer_km: 20082.5,
    is_charging: false,
    charging_power_kw: null,
  },
  signal_recorded_at: {
    soc_percent: "2026-11-06T18:52:00Z",
    range_km: "2026-11-06T18:51:00Z",
    odometer_km: "2026-11-06T18:50:00Z",
    is_charging: "2026-11-06T18:52:00Z",
  },
};

const awaiting: VehicleState = {
  vehicle_id: vehicle.id,
  recorded_at: null,
  received_at: null,
  source: null,
  is_stale: true,
  signals: {
    soc_percent: null,
    range_km: null,
    odometer_km: null,
    is_charging: null,
    charging_power_kw: null,
  },
  signal_recorded_at: {},
};

function card(name: string) {
  return screen.getByRole("region", { name });
}

describe("VehicleStateView", () => {
  it("renders each value with its unit and source timestamp (metric)", () => {
    render(<VehicleStateView vehicle={vehicle} state={fresh} units="metric" />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("2026 Toyota bZ XLE");
    expect(card("Battery")).toHaveTextContent("72%");
    expect(card("Range")).toHaveTextContent("296km");
    expect(card("Range")).toHaveTextContent("Manufacturer estimate");
    expect(card("Odometer")).toHaveTextContent(`${(20083).toLocaleString()}km`);
    expect(card("Charging")).toHaveTextContent("Not charging");

    expect(within(card("Battery")).getByText(/As of/).querySelector("time")).toHaveAttribute(
      "datetime",
      "2026-11-06T18:52:00Z",
    );
    expect(within(card("Range")).getByText(/As of/).querySelector("time")).toHaveAttribute(
      "datetime",
      "2026-11-06T18:51:00Z",
    );
    expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("awaiting-first-update")).not.toBeInTheDocument();
  });

  it("converts distances to miles for imperial users", () => {
    render(<VehicleStateView vehicle={vehicle} state={fresh} units="imperial" />);
    expect(card("Range")).toHaveTextContent("184mi");
    expect(card("Odometer")).toHaveTextContent(`${(12479).toLocaleString()}mi`);
  });

  it("shows charging power as unavailable when the car doesn't report it", () => {
    const charging = { ...fresh, signals: { ...fresh.signals, is_charging: true } };
    render(<VehicleStateView vehicle={vehicle} state={charging} units="metric" />);
    expect(card("Charging")).toHaveTextContent("Charging");
    expect(card("Charging")).toHaveTextContent("Charging power unavailable");
  });

  it("shows charging power with its own timestamp, not the status timestamp", () => {
    // Power last reported in an earlier session; status is newer.
    const charging: VehicleState = {
      ...fresh,
      signals: { ...fresh.signals, is_charging: true, charging_power_kw: 7.2 },
      signal_recorded_at: {
        ...fresh.signal_recorded_at,
        is_charging: "2026-11-06T18:52:00Z",
        charging_power_kw: "2026-11-05T09:00:00Z",
      },
    };
    render(<VehicleStateView vehicle={vehicle} state={charging} units="metric" />);
    const power = within(card("Charging")).getByTestId("charging-power");
    expect(power).toHaveTextContent("7.2 kW");
    expect(power.querySelector("time")).toHaveAttribute("datetime", "2026-11-05T09:00:00Z");
    expect(within(card("Charging")).getByText(/As of/).querySelector("time")).toHaveAttribute(
      "datetime",
      "2026-11-06T18:52:00Z",
    );
  });

  it("says a missing signal is not reported instead of showing a number", () => {
    const partial: VehicleState = {
      ...fresh,
      signals: { ...fresh.signals, range_km: null },
      signal_recorded_at: { soc_percent: "2026-11-06T18:52:00Z" },
    };
    render(<VehicleStateView vehicle={vehicle} state={partial} units="metric" />);
    expect(card("Range")).toHaveTextContent("Not reported");
    expect(card("Range")).toHaveTextContent("No reading yet");
  });

  it("shows the stale banner when data is stale", () => {
    render(
      <VehicleStateView vehicle={vehicle} state={{ ...fresh, is_stale: true }} units="metric" />,
    );
    const banner = screen.getByTestId("stale-banner");
    expect(banner).toHaveTextContent(/Data may be stale — last update/);
    expect(banner.querySelector("time")).toHaveAttribute("datetime", fresh.recorded_at);
    // Values are still shown, just flagged.
    expect(card("Battery")).toHaveTextContent("72%");
  });

  it("shows the awaiting-first-update state with no cards", () => {
    render(<VehicleStateView vehicle={vehicle} state={awaiting} units="imperial" />);
    expect(screen.getByTestId("awaiting-first-update")).toHaveTextContent(
      "Connected — awaiting first update from your vehicle.",
    );
    expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Battery" })).not.toBeInTheDocument();
  });

  it("links to Plan Trip, Find Chargers and the vehicle", () => {
    render(<VehicleStateView vehicle={vehicle} state={fresh} units="metric" />);
    const nav = screen.getByRole("navigation", { name: "Quick links" });
    expect(within(nav).getByRole("link", { name: "Plan Trip" })).toHaveAttribute("href", "/trips");
    expect(within(nav).getByRole("link", { name: "Find Chargers" })).toHaveAttribute(
      "href",
      "/charging",
    );
    expect(within(nav).getByRole("link", { name: "View Vehicle" })).toHaveAttribute(
      "href",
      `/garage/${vehicle.id}`,
    );
  });
});

describe("NoVehicle", () => {
  it("links to the Garage", () => {
    render(<NoVehicle />);
    expect(screen.getByRole("heading", { name: "No vehicle connected" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Garage" })).toHaveAttribute("href", "/garage");
  });
});

describe("pickDashboardVehicle", () => {
  const v = (id: string, status: VehicleSummary["connection_status"] = "connected") => ({
    id,
    connection_status: status,
  });

  it("prefers the primary vehicle when it is connected", () => {
    expect(pickDashboardVehicle([v("a"), v("b")], "b")?.id).toBe("b");
  });

  it("falls back to the most recent connected vehicle", () => {
    expect(pickDashboardVehicle([v("a"), v("b")], null)?.id).toBe("a");
    expect(pickDashboardVehicle([v("a"), v("b")], "missing")?.id).toBe("a");
    expect(pickDashboardVehicle([v("a"), v("b", "disconnected")], "b")?.id).toBe("a");
  });

  it("returns null when nothing is connected", () => {
    expect(pickDashboardVehicle([], null)).toBeNull();
    expect(pickDashboardVehicle([v("a", "pending")], "a")).toBeNull();
  });
});

describe("formatTimeAgo", () => {
  const now = Date.parse("2026-11-06T20:00:00Z");
  it("formats relative times", () => {
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    expect(formatTimeAgo("2026-11-06T19:40:00Z", now)).toBe(rtf.format(-20, "minute"));
    expect(formatTimeAgo("2026-11-06T17:00:00Z", now)).toBe(rtf.format(-3, "hour"));
    expect(formatTimeAgo("2026-11-03T20:00:00Z", now)).toBe(rtf.format(-3, "day"));
  });
  it("returns the input for a bad timestamp", () => {
    expect(formatTimeAgo("nope", now)).toBe("nope");
  });
});
