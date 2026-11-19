import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MapViewProps } from "@/components/map/types";
import { TripDetail } from "@/components/trips/trip-detail";

import { feasiblePlan, infeasiblePlan, makeDetail, STATION_ID, TRIP_ID } from "./trip-fixture";

const mapProps = vi.fn();
vi.mock("@/components/map/lazy-map-view", () => ({
  LazyMapView: (props: MapViewProps) => {
    mapProps(props);
    return <div data-testid="map-stub" />;
  },
}));

const deleteTrip = vi.fn();
vi.mock("@/components/trips/actions", () => ({
  deleteTrip: (id: string) => deleteTrip(id),
}));

function row(label: string) {
  return screen.getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement;
}

describe("TripDetail", () => {
  beforeEach(() => {
    mapProps.mockReset();
    deleteTrip.mockReset();
  });

  it("renders the stored feasible plan: map, totals and stops", () => {
    render(<TripDetail trip={makeDetail(feasiblePlan())} units="metric" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Frederick, MD → Toronto, ON",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    const props = mapProps.mock.calls.at(-1)![0] as MapViewProps;
    expect(props.routes).toEqual([{ index: 0, geometry_polyline6: "_c`|@_c`|@" }]);
    expect(props.stations).toEqual([
      expect.objectContaining({ id: STATION_ID, location: { lat: 40.2732, lon: -76.8867 } }),
    ]);
    expect(props.center).toEqual({ lat: 39.4143, lon: -77.4105, label: "Frederick, MD" });

    expect(row("Distance")).toHaveTextContent("748 km");
    expect(row("Starting charge")).toHaveTextContent("90% (vehicle telemetry)");
    expect(row("Arrival charge")).toHaveTextContent("15%");
    expect(row("Total time")).toHaveTextContent("9 h 43 min");
    expect(row("Detours (estimate)")).toHaveTextContent("8 min");

    const stops = screen.getByRole("list");
    expect(stops).toHaveTextContent("Electrify America · CCS1 · 150 kW");
    expect(stops).toHaveTextContent("Arrive 16% → leave 61% · charge 15 min");
    expect(stops).toHaveTextContent("detour 2 min (estimate)");
    expect(screen.getByRole("link", { name: "Example station" })).toHaveAttribute(
      "href",
      `/charging/${STATION_ID}`,
    );

    expect(row("Charging curve")).toHaveTextContent("derived from published figures");
    expect(row("Stations")).toHaveTextContent("41 considered; 12 incompatible, 3 with unknown power");
  });

  it("says when no charging is needed", () => {
    render(<TripDetail trip={makeDetail({ ...feasiblePlan(), stops: [] })} units="metric" />);
    expect(screen.getByText("No charging needed on this trip.")).toBeInTheDocument();
  });

  it("shows an infeasible saved trip with its reason prominently", () => {
    render(<TripDetail trip={makeDetail(infeasiblePlan())} units="metric" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Destination unreachable");
    expect(alert).toHaveTextContent("No charger is close enough to cross the gap after km 412");
    expect(alert).toHaveTextContent("from 412 km to 640 km");
    expect(alert).toHaveTextContent("needs 79% charge, 80% available");
    expect(screen.queryByText("Charging stops")).not.toBeInTheDocument();
    expect(screen.queryByText("Total time")).not.toBeInTheDocument();
    expect(row("Driving")).toHaveTextContent("8 h 35 min");
  });

  it("has no map when there was no driving route", () => {
    render(
      <TripDetail
        trip={makeDetail({
          ...infeasiblePlan(),
          route: null,
          reason: { code: "NO_DRIVING_ROUTE", message: "No drivable route.", limiting_segment: null },
        })}
        units="metric"
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("No driving route");
    expect(screen.queryByTestId("map-stub")).not.toBeInTheDocument();
    expect(screen.getByText(/this plan has no route/)).toBeInTheDocument();
  });

  it("says so when the stored plan can't be read", () => {
    render(<TripDetail trip={makeDetail(feasiblePlan(), { plan: [] as never })} units="metric" />);
    expect(screen.getByRole("alert")).toHaveTextContent("couldn't be read");
  });

  it("deletes after confirmation and shows an error if it fails", async () => {
    deleteTrip.mockResolvedValue({ error: "Couldn't delete the trip. Please try again." });
    render(<TripDetail trip={makeDetail(feasiblePlan())} units="metric" />);

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Delete this trip?");
    expect(deleteTrip).not.toHaveBeenCalled();

    const confirm = screen
      .getAllByRole("button", { name: "Delete" })
      .find((b) => b.closest("[role=alertdialog]"))!;
    fireEvent.click(confirm);
    expect(deleteTrip).toHaveBeenCalledWith(TRIP_ID);
    await waitFor(() =>
      expect(screen.getByRole("alertdialog")).toHaveTextContent("Couldn't delete the trip."),
    );
  });
});
