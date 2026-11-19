import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnalyticsCharts } from "@/components/analytics/analytics-charts";
import { BarChart } from "@/components/analytics/charts/bar-chart";
import { PLOT } from "@/components/analytics/charts/frame";
import { LineChart } from "@/components/analytics/charts/line-chart";
import { ScatterChart } from "@/components/analytics/charts/scatter-chart";
import type {
  AnalyticsTrip,
  ConsumptionBucket,
} from "@/components/analytics/format";
import { SimulatedNotice } from "@/components/analytics/simulated-notice";
import { SummaryCards } from "@/components/analytics/summary-cards";
import { TripsTable } from "@/components/analytics/trips-table";

function makeTrip(overrides: Partial<AnalyticsTrip> = {}): AnalyticsTrip {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    vehicle_id: "22222222-2222-4222-8222-222222222222",
    start_time: "2026-11-08T10:53:00Z",
    start_time_latest: "2026-11-08T10:53:00Z",
    end_time: "2026-11-08T13:00:00Z",
    distance_km: 183.6,
    soc_start_percent: 66,
    soc_end_percent: 15,
    observed_energy_kwh: 36.21,
    observed_energy_uncertainty_kwh: 0.71,
    estimated_energy_kwh: 36.72,
    error_kwh: -0.51,
    observed_kwh_per_km: 0.197,
    energy_source: "soc_inferred",
    quality_flag: "high",
    quality_reasons: [],
    review_status: "auto",
    in_progress: false,
    included_in_aggregates: true,
    planned_trip_id: null,
    plan_origin_label: null,
    plan_destination_label: null,
    model_version: "deterministic-v1",
    is_simulated: false,
    ...overrides,
  };
}

function bucket(overrides: Partial<ConsumptionBucket>): ConsumptionBucket {
  return {
    bucket: "2026-W45",
    bucket_start: "2026-11-02T00:00:00Z",
    trips: 0,
    distance_km: 0,
    energy_trips: 0,
    observed_energy_kwh: null,
    estimated_energy_kwh: null,
    observed_kwh_per_km: null,
    estimated_kwh_per_km: null,
    mae_kwh: null,
    bias_kwh: null,
    ...overrides,
  };
}

describe("BarChart", () => {
  it("draws one titled bar per bucket and a matching data table", () => {
    render(
      <BarChart
        label="Distance per week"
        yLabel="km"
        categoryHeader="Week"
        valueHeader="Distance (km)"
        bars={[
          { label: "2026-W45", value: 120 },
          { label: "2026-W46", value: 0 },
        ]}
        format={(v) => `${v} km`}
      />,
    );
    const svg = screen.getByRole("img", { name: "Distance per week" });
    const bars = svg.querySelectorAll("rect");
    expect(bars).toHaveLength(2);
    expect(bars[0].querySelector("title")).toHaveTextContent(
      "2026-W45: 120 km",
    );
    expect(Number(bars[1].getAttribute("height"))).toBe(0);

    const table = screen.getByRole("table", { name: "Distance per week" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(
      within(table).getByRole("rowheader", { name: "2026-W45" }),
    ).toBeInTheDocument();
    expect(within(table).getByText("120 km")).toBeInTheDocument();
  });

  it("keeps the data table in the DOM and lets sighted users show it", () => {
    render(
      <BarChart
        label="D"
        yLabel="km"
        categoryHeader="Day"
        valueHeader="km"
        bars={[{ label: "a", value: 1 }]}
        format={String}
      />,
    );
    const toggle = screen.getByRole("button", { name: "Show data" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("table").parentElement).toHaveClass("sr-only");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide data" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("table").parentElement).not.toHaveClass("sr-only");
  });
});

describe("LineChart", () => {
  it("breaks lines at empty buckets and lists — in the table", () => {
    render(
      <LineChart
        label="Consumption"
        yLabel="Wh/mi"
        categoryHeader="Week"
        labels={["W1", "W2", "W3"]}
        series={[
          { name: "Observed", variant: "primary", values: [300, null, 310] },
          { name: "Estimated", variant: "secondary", values: [320, null, 315] },
        ]}
        format={(v) => `${v} Wh/mi`}
      />,
    );
    const svg = screen.getByRole("img", { name: "Consumption" });
    const observed = svg.querySelector('[data-series="Observed"]')!;
    expect(observed.querySelectorAll("polyline")).toHaveLength(2);
    expect(observed.querySelectorAll("circle")).toHaveLength(2);
    expect(observed.querySelector("circle title")).toHaveTextContent(
      "W1, Observed: 300 Wh/mi",
    );

    const table = screen.getByRole("table", { name: "Consumption" });
    const w2 = within(table)
      .getByRole("rowheader", { name: "W2" })
      .closest("tr")!;
    expect(within(w2).getAllByText("—")).toHaveLength(2);
  });
});

describe("ScatterChart", () => {
  it("draws a y = x line and hollow points for low quality", () => {
    render(
      <ScatterChart
        label="Per drive"
        xLabel="Estimated kWh"
        yLabel="Observed kWh"
        columns={["Drive", "Estimated", "Observed", "Quality"]}
        points={[
          {
            id: "a",
            rowLabel: "Nov 8",
            x: 36.72,
            y: 36.21,
            hollow: false,
            note: "High quality",
          },
          {
            id: "b",
            rowLabel: "Nov 9",
            x: 2,
            y: 3.1,
            hollow: true,
            note: "Low quality",
          },
        ]}
        format={(v) => v.toFixed(2)}
      />,
    );
    const svg = screen.getByRole("img", { name: "Per drive" });
    const ref = svg.querySelector('[data-reference="y=x"]')!;
    // Same domain on both axes, so the reference runs corner to corner.
    expect(ref.getAttribute("x1")).toBe(String(PLOT.left));
    expect(ref.getAttribute("y2")).toBe(String(PLOT.top));
    expect(svg.querySelectorAll('circle[data-hollow="true"]')).toHaveLength(1);
    expect(
      svg.querySelector('circle[data-hollow="true"] title'),
    ).toHaveTextContent("Nov 9: estimated 2.00, observed 3.10 (Low quality)");
    expect(screen.getByRole("table", { name: "Per drive" })).toHaveTextContent(
      "Low quality",
    );
  });
});

describe("AnalyticsCharts", () => {
  it("converts to the user's units and skips in-progress drives in the scatter", () => {
    render(
      <AnalyticsCharts
        series={[
          bucket({
            distance_km: 160.9344,
            observed_kwh_per_km: 0.2,
            estimated_kwh_per_km: 0.21,
            trips: 1,
            energy_trips: 1,
          }),
        ]}
        trips={[
          makeTrip(),
          makeTrip({
            id: "33333333-3333-4333-8333-333333333333",
            in_progress: true,
          }),
        ]}
        groupBy="week"
        units="imperial"
        tz="UTC"
        partialTrips={false}
      />,
    );
    expect(
      screen.getByRole("table", { name: /Distance driven per week, in mi/ }),
    ).toHaveTextContent("100.0 mi");
    expect(
      screen.getByRole("table", { name: /consumption per week, in Wh\/mi/ }),
    ).toHaveTextContent("322 Wh/mi");
    const scatter = screen.getByRole("img", {
      name: /Estimated vs observed energy/,
    });
    expect(scatter.querySelectorAll("circle")).toHaveLength(1);
  });

  it("explains an empty scatter", () => {
    render(
      <AnalyticsCharts
        series={[]}
        trips={[makeTrip({ observed_energy_kwh: null })]}
        groupBy="day"
        units="metric"
        tz="UTC"
        partialTrips
      />,
    );
    expect(
      screen.getByText(
        /No drives with both an observed and an estimated energy/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Only the drives in the table below are shown/),
    ).toBeInTheDocument();
  });
});

describe("TripsTable", () => {
  it("shows a quality badge with the reasons as its description", () => {
    const trip = makeTrip({
      quality_flag: "low",
      quality_reasons: ["soc_delta_small", "start_time_bounded"],
      review_status: "needs_review",
      included_in_aggregates: false,
      start_time: "2026-11-08T08:00:00Z",
    });
    render(<TripsTable trips={[trip]} units="metric" />);
    const badge = screen.getByText("Low quality");
    expect(badge).toHaveAttribute("tabindex", "0");
    expect(badge).toHaveAccessibleDescription(
      /Used under 10 % of the battery.*Exact start time unknown/,
    );
    expect(screen.getByText(/Started between/)).toBeInTheDocument();
    expect(screen.getByText("Not in totals")).toBeInTheDocument();
  });

  it("links needs-review drives to the matched trip, or to saved trips", () => {
    render(
      <TripsTable
        trips={[
          makeTrip({
            review_status: "needs_review",
            planned_trip_id: "trip-1",
            plan_origin_label: "A",
            plan_destination_label: "B",
          }),
          makeTrip({
            id: "44444444-4444-4444-8444-444444444444",
            review_status: "needs_review",
          }),
        ]}
        units="metric"
      />,
    );
    const links = screen.getAllByRole("link", { name: "Needs review" });
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/trips/trip-1",
      "/trips/saved",
    ]);
    expect(screen.getByRole("link", { name: "Plan: A → B" })).toHaveAttribute(
      "href",
      "/trips/trip-1",
    );
  });

  it("shows the measured numbers and — for missing ones", () => {
    render(
      <TripsTable
        trips={[
          makeTrip(),
          makeTrip({
            id: "55555555-5555-4555-8555-555555555555",
            observed_energy_kwh: null,
            observed_energy_uncertainty_kwh: null,
            estimated_energy_kwh: null,
            error_kwh: null,
            observed_kwh_per_km: null,
            soc_start_percent: null,
            soc_end_percent: null,
            is_simulated: true,
            in_progress: true,
          }),
        ]}
        units="metric"
      />,
    );
    const [first, second] = screen.getAllByRole("row").slice(1);
    expect(first).toHaveTextContent("184 km");
    expect(first).toHaveTextContent("66 → 15 %");
    expect(first).toHaveTextContent("36.21 kWh ± 0.71");
    expect(first).toHaveTextContent("36.72 kWh");
    expect(first).toHaveTextContent("−0.51 kWh");
    expect(first).toHaveTextContent("19.7 kWh/100 km");
    expect(first).toHaveTextContent("Automatic");
    expect(within(second).getAllByText("—").length).toBeGreaterThanOrEqual(5);
    expect(second).toHaveTextContent("Simulated");
    expect(second).toHaveTextContent("In progress");
  });
});

describe("SimulatedNotice", () => {
  it("says how many simulated trips were left out", () => {
    render(
      <SimulatedNotice
        includesSimulated={false}
        excludedSimulatedTrips={3}
        includeHref="/in"
        excludeHref="/out"
      />,
    );
    expect(screen.getByTestId("simulated-excluded")).toHaveTextContent(
      "3 simulated trips in this range are left out.",
    );
    expect(
      screen.getByRole("link", { name: "Include simulated data" }),
    ).toHaveAttribute("href", "/in");
  });

  it("shows a banner when simulated data is included", () => {
    render(
      <SimulatedNotice
        includesSimulated
        excludedSimulatedTrips={0}
        includeHref="/in"
        excludeHref="/out"
      />,
    );
    expect(screen.getByTestId("simulated-banner")).toHaveTextContent(
      "Includes simulated data.",
    );
    expect(
      screen.getByRole("link", { name: "Show real trips only" }),
    ).toHaveAttribute("href", "/out");
  });

  it("shows nothing when there is no simulated data", () => {
    const { container } = render(
      <SimulatedNotice
        includesSimulated={false}
        excludedSimulatedTrips={0}
        includeHref="/in"
        excludeHref="/out"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("SummaryCards", () => {
  it("shows totals in the user's units, the model's bias and plan accuracy", () => {
    render(
      <SummaryCards
        totals={{
          trips: 2,
          distance_km: 406,
          energy_trips: 2,
          observed_energy_kwh: 80.23,
          estimated_energy_kwh: 81.2,
          observed_kwh_per_km: 0.198,
          estimated_kwh_per_km: 0.2,
          mae_kwh: 0.485,
          bias_kwh: -0.485,
        }}
        plans={{
          matched: 1,
          with_energy: 1,
          mae_kwh: 0.29,
          mean_arrival_soc_error_points: -0.3,
        }}
        units="metric"
      />,
    );
    const summary = screen.getByTestId("analytics-summary");
    expect(summary).toHaveTextContent("406 km");
    expect(summary).toHaveTextContent("19.8 kWh/100 km");
    expect(summary).toHaveTextContent("20.0 kWh/100 km");
    expect(summary).toHaveTextContent("0.49 kWh");
    expect(summary).toHaveTextContent("Bias −0.49 kWh (model over-estimates)");
    expect(summary).toHaveTextContent("Arrival battery -0.3 points");
  });
});
