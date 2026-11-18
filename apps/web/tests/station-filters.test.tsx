import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StationFilters } from "@/components/stations/station-filters";
import { EMPTY_FILTERS, type ChargingFilters } from "@/lib/stations";

function setup(filters: Partial<ChargingFilters> = {}, vehicleConnectors = ["NACS", "CCS1"] as const) {
  const onChange = vi.fn();
  render(
    <StationFilters
      filters={{ ...EMPTY_FILTERS, ...filters }}
      units="imperial"
      vehicleConnectors={vehicleConnectors}
      networkOptions={["Tesla", "Electrify America"]}
      onChange={onChange}
    />,
  );
  return { onChange };
}

const pressed = (name: string) => screen.getByRole("button", { name, pressed: true });
const notPressed = (name: string) => screen.getByRole("button", { name, pressed: false });

describe("StationFilters", () => {
  it("selects the vehicle's connectors by default", () => {
    setup();
    expect(pressed("CCS1")).toBeInTheDocument();
    expect(pressed("NACS (Tesla)")).toBeInTheDocument();
    expect(notPressed("CHAdeMO")).toBeInTheDocument();
    expect(notPressed("J1772")).toBeInTheDocument();
    expect(screen.getByText("Showing your vehicle's connectors.")).toBeInTheDocument();
  });

  it("selects every connector type when there is no vehicle", () => {
    setup({}, [] as never);
    expect(screen.queryAllByRole("button", { pressed: true })).toHaveLength(0);
    expect(screen.getByText("Showing every connector type.")).toBeInTheDocument();
  });

  it("toggling a connector makes an explicit choice", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: "CHAdeMO" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ connectors: ["CCS1", "NACS", "CHAdeMO"] }),
    );
  });

  it("offers 'Any connector' and going back to the vehicle default", () => {
    const { onChange } = setup({ connectors: ["J1772"] });
    fireEvent.click(screen.getByRole("button", { name: "Any connector" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ connectors: "any" }));
    fireEvent.click(screen.getByRole("button", { name: "Use my vehicle's connectors" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ connectors: null }));
  });

  it("changes network, level, power and radius", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByLabelText("Network"), { target: { value: "Tesla" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ networks: ["Tesla"] }));
    fireEvent.change(screen.getByLabelText("Charging level"), { target: { value: "dc_fast" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ level: "dc_fast" }));
    fireEvent.change(screen.getByLabelText("Minimum power"), { target: { value: "150" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ minPowerKw: 150 }));
    fireEvent.change(screen.getByLabelText("Radius"), { target: { value: "8.05" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ radiusKm: 8.05 }));
  });

  it("shows the radius in the user's units", () => {
    setup();
    const radius = screen.getByLabelText("Radius") as HTMLSelectElement;
    expect([...radius.options].map((o) => o.text)).toEqual(["5 mi", "10 mi", "25 mi", "50 mi"]);
    expect(radius.selectedOptions[0].text).toBe("25 mi");
  });
});
