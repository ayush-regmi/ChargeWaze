import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PlanPanel } from "@/app/(app)/trips/_components/plan-panel";
import { TripPlanner } from "@/app/(app)/trips/_components/trip-planner";
import type { MapViewProps } from "@/components/map/types";
import type { Preferences } from "@/components/profile/preferences";
import type { PlanOutcome, PlanResponse } from "@/lib/plan-format";
import { EMPTY_TRIP } from "@/lib/trip-url";

import { feasiblePlan, infeasiblePlan, TRIP_ID, VEHICLE_ID } from "./trip-fixture";

const planActions = vi.hoisted(() => ({ planTrip: vi.fn(), saveTrip: vi.fn() }));
vi.mock("@/app/(app)/trips/_components/plan-actions", () => planActions);
const tripActions = vi.hoisted(() => ({ getDirections: vi.fn(), getStationsAlongRoute: vi.fn() }));
vi.mock("@/app/(app)/trips/_components/actions", () => tripActions);
vi.mock("@/components/places/actions", () => ({ suggestPlaces: vi.fn(), retrievePlace: vi.fn() }));
const mapProps = vi.fn();
vi.mock("@/components/map/lazy-map-view", () => ({
  LazyMapView: (props: MapViewProps) => {
    mapProps(props);
    return <div data-testid="map-stub" />;
  },
}));

const FREDERICK = { lat: 39.3847, lon: -77.4055, label: "Tesla Supercharger" };
const TORONTO = { lat: 43.6479, lon: -79.3835, label: "Toronto" };
const PREFS: Preferences = {
  units: "imperial",
  reserve_soc: 0.15,
  preferred_charge_to: 0.8,
  max_detour_minutes: 20,
  preferred_networks: ["EVgo"],
  primary_vehicle_id: null,
};
/** Two points on the equator, 0° and 2° east (precision-6 polyline). */
const EQUATOR_LINE = "???_gayB";
const feasible = () => feasiblePlan() as unknown as PlanResponse;
const infeasible = () => infeasiblePlan() as unknown as PlanResponse;

function Harness({
  initial = null,
  vehicleId = VEHICLE_ID,
}: {
  initial?: PlanOutcome | null;
  vehicleId?: string | null;
}) {
  // Keeps the outcome like TripPlanner does.
  const [outcome, setOutcome] = useState(initial);
  return (
    <PlanPanel
      vehicleId={vehicleId}
      vehicleTitle="2026 Toyota bZ"
      origin={FREDERICK}
      destination={TORONTO}
      routeIndex={0}
      routeName="Route A"
      preferences={PREFS}
      units="imperial"
      outcome={outcome}
      request={null}
      onOutcome={(o) => setOutcome(o)}
      signInHref="/sign-in?next=%2Ftrips"
    />
  );
}

beforeEach(() => {
  planActions.planTrip.mockReset();
  planActions.saveTrip.mockReset();
  mapProps.mockReset();
});

describe("PlanPanel", () => {
  it("asks to connect a vehicle when there is none", () => {
    render(<Harness vehicleId={null} />);
    expect(screen.queryByRole("button", { name: "Plan charging stops" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a vehicle" })).toHaveAttribute(
      "href",
      "/garage",
    );
  });

  it("plans the route and shows stops, totals and assumptions", async () => {
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan: feasible() });
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Plan charging stops" }));

    const result = await screen.findByTestId("plan-result");
    expect(planActions.planTrip).toHaveBeenCalledWith({
      vehicleId: VEHICLE_ID,
      origin: FREDERICK,
      destination: TORONTO,
      routeIndex: 0,
      preferences: null,
      startingSocOverride: null,
    });
    expect(result).toHaveAttribute("data-status", "feasible");
    expect(within(result).getByText("1 charging stop · arrive with 15%")).toBeInTheDocument();
    const stop = within(result).getByTestId("plan-stop");
    expect(within(stop).getByTestId("plan-stop-soc")).toHaveTextContent("16% → 61%");
    expect(stop).toHaveTextContent("charge 15 min");
    expect(stop).toHaveTextContent("detour 2 min (estimate)");
    expect(within(stop).getByRole("link", { name: "Example station" })).toHaveAttribute(
      "href",
      expect.stringContaining("/charging/"),
    );
    const totals = within(result).getByTestId("plan-totals");
    expect(totals).toHaveTextContent("Total time9 h 43 min");
    expect(totals).toHaveTextContent("Detours (estimate)8 min");
    expect(totals).toHaveTextContent("Distance465 mi");
    const assumptions = within(result).getByTestId("plan-assumptions");
    expect(assumptions).toHaveTextContent("derived from published figures, not measured");
    expect(assumptions).toHaveTextContent("3 with unknown power");
    expect(screen.getByRole("button", { name: "Plan again" })).toBeInTheDocument();
  });

  it("shows an infeasible plan prominently with its reason and no totals", () => {
    render(<Harness initial={{ kind: "plan", plan: infeasible() }} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Not feasible: Destination unreachable");
    expect(alert).toHaveTextContent("No charger is close enough");
    expect(alert).toHaveTextContent("needs 79% charge with 80% available");
    expect(screen.queryByTestId("plan-totals")).not.toBeInTheDocument();
    expect(screen.queryByTestId("plan-stop")).not.toBeInTheDocument();
  });

  it("prompts for the starting charge and re-plans with the override", async () => {
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan: feasible() });
    render(
      <Harness
        initial={{ kind: "soc_required", lastSoc: 0.64, lastRecordedAt: "2026-11-07T10:01:00Z" }}
      />,
    );
    const prompt = screen.getByTestId("plan-soc-prompt");
    expect(prompt).toHaveTextContent("64%");
    const input = within(prompt).getByLabelText("Starting battery (%)");
    expect(input).toHaveValue("64");

    fireEvent.change(input, { target: { value: "120" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "Plan with this charge" }));
    expect(within(prompt).getByRole("alert")).toHaveTextContent("between 0 and 100");
    expect(planActions.planTrip).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "90" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "Plan with this charge" }));
    await screen.findByTestId("plan-result");
    expect(planActions.planTrip).toHaveBeenCalledWith(
      expect.objectContaining({ startingSocOverride: 0.9 }),
    );
  });

  it("says there is no reading when the car never reported one", () => {
    render(<Harness initial={{ kind: "soc_required", lastSoc: null, lastRecordedAt: null }} />);
    expect(screen.getByTestId("plan-soc-prompt")).toHaveTextContent(
      "We don't have a battery reading from your car.",
    );
    expect(screen.getByLabelText("Starting battery (%)")).toHaveValue("");
  });

  it("names missing vehicle specs and links to the Garage", () => {
    render(
      <Harness initial={{ kind: "specs_incomplete", missing: ["charging_curve", "connectors"] }} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "missing charging curve, charging connectors",
    );
    expect(screen.getByRole("link", { name: "Add them in the Garage" })).toHaveAttribute(
      "href",
      `/garage/${VEHICLE_ID}`,
    );
  });

  it("retries a provider error, and offers sign-in when the session ended", async () => {
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan: feasible() });
    const { unmount } = render(
      <Harness initial={{ kind: "error", code: "STATIONS_UNREACHABLE" }} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("charger directory isn't responding");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByTestId("plan-result");
    unmount();

    render(<Harness initial={{ kind: "error", code: "UNAUTHENTICATED" }} />);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in?next=%2Ftrips",
    );
  });

  it("says when the plan limit is hit and when to try again", async () => {
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan: feasible() });
    render(<Harness initial={{ kind: "rate_limited", retryAfterSeconds: 95 }} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Too many trip plans in a short time. Try again in 2 minutes.");
    expect(alert).not.toHaveTextContent("Couldn't plan this trip");
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await screen.findByTestId("plan-result");
  });

  it("re-plans with only the changed preferences and keeps an override", async () => {
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan: infeasible() });
    render(<Harness initial={{ kind: "plan", plan: feasible() }} />);
    const form = screen.getByRole("form", { name: "Re-plan with different preferences" });

    fireEvent.change(within(form).getByLabelText("Reserve (%)"), { target: { value: "80" } });
    fireEvent.click(within(form).getByRole("button", { name: "Re-plan" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("lower than the charge-to");
    expect(planActions.planTrip).not.toHaveBeenCalled();

    fireEvent.change(within(form).getByLabelText("Reserve (%)"), { target: { value: "50" } });
    fireEvent.change(within(form).getByLabelText("Charge to (%)"), { target: { value: "55" } });
    fireEvent.change(within(form).getByLabelText("Starting battery (%)"), {
      target: { value: "70" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Re-plan" }));
    await waitFor(() => expect(screen.getByTestId("plan-infeasible")).toBeInTheDocument());
    expect(planActions.planTrip).toHaveBeenCalledWith(
      expect.objectContaining({
        preferences: { reserve_soc: 0.5, preferred_charge_to: 0.55 },
        startingSocOverride: 0.7,
      }),
    );
  });

  it("saves the trip and links to it", async () => {
    planActions.saveTrip.mockResolvedValue({ ok: true });
    render(<Harness initial={{ kind: "plan", plan: feasible() }} />);
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    expect(await screen.findByRole("link", { name: "View trip" })).toHaveAttribute(
      "href",
      `/trips/${TRIP_ID}`,
    );
    expect(planActions.saveTrip).toHaveBeenCalledWith(TRIP_ID);
  });

  it("shows a save error", async () => {
    planActions.saveTrip.mockResolvedValue({ ok: false, error: "UNKNOWN" });
    render(<Harness initial={{ kind: "plan", plan: feasible() }} />);
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    expect(await screen.findByText("Couldn't save this trip. Try again.")).toBeInTheDocument();
  });
});

describe("TripPlanner with a plan", () => {
  const routes = [0, 1].map((index) => ({
    index,
    geometry_polyline6: EQUATOR_LINE,
    distance_m: 222_000,
    duration_s: 9000,
    summary: null,
    countries: ["US"],
  }));

  beforeEach(() => {
    tripActions.getDirections.mockResolvedValue({ routes, noRouteReason: null });
    tripActions.getStationsAlongRoute.mockResolvedValue({
      items: [],
      totalResults: 0,
      unknownPowerCount: 0,
      truncated: false,
    });
  });

  function renderPlanner() {
    return render(
      <TripPlanner
        initial={{ ...EMPTY_TRIP, origin: FREDERICK, destination: TORONTO }}
        units="imperial"
        vehicle={{ id: VEHICLE_ID, title: "2026 Toyota bZ", connectors: ["NACS"] }}
        preferences={PREFS}
      />,
    );
  }

  const lastMap = () => mapProps.mock.calls[mapProps.mock.calls.length - 1][0] as MapViewProps;

  it("puts the plan's stops on the map, labelled with arrival → departure charge", async () => {
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan: feasible() });
    renderPlanner();
    fireEvent.click(await screen.findByRole("button", { name: "Plan charging stops" }));
    await screen.findByTestId("plan-result");
    expect(lastMap().stations).toEqual([
      expect.objectContaining({ kind: "stop", label: "16% → 61%" }),
    ]);
    expect(lastMap().selectedRouteTone).toBe("default");
    expect(lastMap().segments).toEqual([]);
  });

  it("greys the route and draws the limiting segment for an infeasible plan", async () => {
    const plan = infeasible();
    plan.route = { ...plan.route!, geometry_polyline6: EQUATOR_LINE, distance_km: 700 };
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan });
    renderPlanner();
    fireEvent.click(await screen.findByRole("button", { name: "Plan charging stops" }));
    await screen.findByTestId("plan-infeasible");
    expect(lastMap().selectedRouteTone).toBe("infeasible");
    expect(lastMap().segments).toHaveLength(1);
    expect(lastMap().segments![0].coordinates.length).toBeGreaterThanOrEqual(2);
  });

  it("draws the selected route from the plan's own geometry", async () => {
    // Mapbox answered the planner with a different line than the one listed.
    const PLAN_LINE = "_ibE?_ibE_ibE";
    const plan = feasible();
    plan.route = { ...plan.route!, geometry_polyline6: PLAN_LINE };
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan });
    renderPlanner();
    await screen.findByRole("button", { name: "Plan charging stops" });
    expect(lastMap().routes!.map((r) => r.geometry_polyline6)).toEqual([
      EQUATOR_LINE,
      EQUATOR_LINE,
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Plan charging stops" }));
    await screen.findByTestId("plan-result");
    expect(lastMap().selectedRouteIndex).toBe(0);
    // Route A (planned) uses the plan's line; Route B is unchanged.
    expect(lastMap().routes).toEqual([
      { index: 0, geometry_polyline6: PLAN_LINE },
      { index: 1, geometry_polyline6: EQUATOR_LINE },
    ]);
  });

  it("restores a route's plan settings after switching routes and back", async () => {
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan: feasible() });
    renderPlanner();
    fireEvent.click(await screen.findByRole("button", { name: "Plan charging stops" }));
    await screen.findByTestId("plan-result");

    const form = () => screen.getByRole("form", { name: "Re-plan with different preferences" });
    fireEvent.change(within(form()).getByLabelText("Reserve (%)"), { target: { value: "20" } });
    fireEvent.change(within(form()).getByLabelText("Starting battery (%)"), {
      target: { value: "70" },
    });
    fireEvent.click(within(form()).getByRole("button", { name: "Re-plan" }));
    await waitFor(() => expect(planActions.planTrip).toHaveBeenCalledTimes(2));
    await screen.findByTestId("plan-result");
    const settings = { preferences: { reserve_soc: 0.2 }, startingSocOverride: 0.7 };
    expect(planActions.planTrip).toHaveBeenLastCalledWith(expect.objectContaining(settings));

    fireEvent.click(screen.getByRole("button", { name: /Route B/ }));
    await screen.findByRole("heading", { name: "Charging plan for Route B" });
    fireEvent.click(screen.getByRole("button", { name: /Route A/ }));
    await screen.findByRole("heading", { name: "Charging plan for Route A" });

    // The form shows what the displayed plan used, not the profile defaults.
    expect(within(form()).getByLabelText("Reserve (%)")).toHaveValue("20");
    expect(within(form()).getByLabelText("Starting battery (%)")).toHaveValue("70");

    fireEvent.click(screen.getByRole("button", { name: "Plan again" }));
    await waitFor(() => expect(planActions.planTrip).toHaveBeenCalledTimes(3));
    expect(planActions.planTrip).toHaveBeenLastCalledWith(
      expect.objectContaining({ ...settings, routeIndex: 0 }),
    );
  });

  it("keeps each route's plan to itself", async () => {
    planActions.planTrip.mockResolvedValue({ kind: "plan", plan: feasible() });
    renderPlanner();
    fireEvent.click(await screen.findByRole("button", { name: "Plan charging stops" }));
    await screen.findByTestId("plan-result");

    fireEvent.click(screen.getByRole("button", { name: /Route B/ }));
    expect(await screen.findByRole("heading", { name: "Charging plan for Route B" })).toBeVisible();
    expect(screen.queryByTestId("plan-result")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Plan charging stops" })).toBeInTheDocument();
  });
});
