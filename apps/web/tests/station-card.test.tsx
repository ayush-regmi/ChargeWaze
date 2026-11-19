import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StationCard } from "@/components/stations/station-card";

import { makeStation } from "./station-fixture";

vi.mock("@/components/stations/favorite-actions", () => ({
  setStationFavorite: vi.fn().mockResolvedValue(undefined),
}));

describe("StationCard", () => {
  it("shows no star unless told whether the station is a favorite", () => {
    render(<StationCard station={makeStation()} href="/x" />);
    expect(screen.queryByRole("button", { name: /favorites/ })).not.toBeInTheDocument();
  });

  it("shows the star in its state", () => {
    const { rerender } = render(<StationCard station={makeStation()} href="/x" favorite={false} />);
    expect(
      screen.getByRole("button", { name: "Add POTOMAC EDISON MAX KEHNE DC1 to favorites" }),
    ).toHaveAttribute("aria-pressed", "false");
    rerender(<StationCard station={makeStation()} href="/x" favorite />);
    expect(
      screen.getByRole("button", { name: "Remove POTOMAC EDISON MAX KEHNE DC1 from favorites" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("starring doesn't select the card", async () => {
    const onSelect = vi.fn();
    render(<StationCard station={makeStation()} href="/x" favorite={false} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /to favorites/ }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("shows name, network, connectors with count and power, and distance", () => {
    render(<StationCard station={makeStation()} href="/charging/x" distanceText="1.2 mi" />);
    const card = screen.getByRole("article", { name: "POTOMAC EDISON MAX KEHNE DC1" });
    expect(within(card).getByRole("link")).toHaveAttribute("href", "/charging/x");
    expect(card).toHaveTextContent("ChargePoint Network");
    expect(card).toHaveTextContent("1.2 mi");
    const chips = within(card).getByRole("list", { name: "Connectors" });
    expect(chips).toHaveTextContent("CCS1 × 1 · 62 kW");
    expect(chips).toHaveTextContent("CHAdeMO × 1 · 62.5 kW");
    expect(card).toHaveTextContent("Compatible");
    expect(card).not.toHaveTextContent("Power unknown");
  });

  it("shows unknown count and power as unknown, never a made-up number", () => {
    const station = makeStation({
      connectors: [{ type: "J1772", count: null, power_kw: null }],
      max_power_kw: null,
      network: null,
      compatible: null,
    });
    render(<StationCard station={station} href="/charging/x" />);
    const card = screen.getByRole("article");
    expect(card).toHaveTextContent("J1772 count unknown · power unknown");
    expect(card).toHaveTextContent("Power unknown");
    expect(card).toHaveTextContent("Network not reported");
    // No vehicle → no compatibility claim either way.
    expect(card).not.toHaveTextContent(/compatible/i);
  });

  it("never shows availability when the provider gives none", () => {
    render(<StationCard station={makeStation({ live_availability: null })} href="/x" />);
    expect(screen.getByRole("article")).not.toHaveTextContent(/availab/i);
  });

  it("marks incompatible stations", () => {
    render(<StationCard station={makeStation({ compatible: false })} href="/x" />);
    expect(screen.getByRole("article")).toHaveTextContent("Not compatible");
  });

  it("reports selection", () => {
    const onSelect = vi.fn();
    render(<StationCard station={makeStation()} href="/x" selected onSelect={onSelect} />);
    const card = screen.getByRole("article");
    expect(card).toHaveAttribute("aria-current", "true");
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledWith(makeStation().id);
  });
});
