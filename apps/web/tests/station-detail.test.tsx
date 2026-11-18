import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StationDetail } from "@/components/stations/station-detail";

import { makeStation } from "./station-fixture";

function row(label: string) {
  const term = screen.getByText(label, { selector: "dt" });
  return term.nextElementSibling as HTMLElement;
}

describe("StationDetail", () => {
  it("shows connectors, network, address, access, status and fetched time", () => {
    render(<StationDetail station={makeStation({ operator: "Potomac Edison" })} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("POTOMAC EDISON");
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("CCS1162 kW");
    expect(rows[2]).toHaveTextContent("CHAdeMO162.5 kW");
    expect(row("Network")).toHaveTextContent("ChargePoint Network");
    expect(row("Operator")).toHaveTextContent("Potomac Edison");
    expect(row("Address")).toHaveTextContent("1108 W 7th St");
    expect(row("Address")).toHaveTextContent("Frederick, MD 21701");
    expect(row("Access")).toHaveTextContent("Public");
    expect(row("Status")).toHaveTextContent("Open");
    expect(row("Fetched").querySelector("time")).toHaveAttribute("dateTime", "2026-11-18T19:00:00Z");
    expect(screen.queryByText(/out of date/)).not.toBeInTheDocument();
  });

  it("leaves missing values empty instead of guessing", () => {
    render(
      <StationDetail
        station={makeStation({
          network: null,
          operator: null,
          street_address: null,
          city: null,
          region: null,
          postal_code: null,
          country: null,
          pricing_text: null,
          source_updated_at: null,
          connectors: [{ type: "NACS", count: null, power_kw: null }],
          max_power_kw: null,
          compatible: null,
        })}
      />,
    );
    for (const label of ["Network", "Operator", "Address", "Pricing", "Provider updated"]) {
      expect(row(label)).toHaveTextContent("Not reported");
    }
    expect(within(screen.getByRole("table")).getAllByRole("row")[1]).toHaveTextContent(
      "NACS (Tesla)UnknownUnknown",
    );
    expect(screen.getByText("Power unknown")).toBeInTheDocument();
    expect(row("Live availability")).toHaveTextContent("Not reported by the provider");
  });

  it("notes stale data", () => {
    render(<StationDetail station={makeStation({ is_stale: true })} />);
    expect(screen.getByRole("status")).toHaveTextContent("may be out of date");
  });

  it("shows restricted access and the provider's access detail", () => {
    render(
      <StationDetail
        station={makeStation({ restricted_access: true, access_detail: "CALL" })}
      />,
    );
    expect(row("Access")).toHaveTextContent("Public (restricted)");
    expect(row("Access")).toHaveTextContent("CALL");
  });

  it("links to external directions in a new tab", () => {
    render(<StationDetail station={makeStation()} />);
    const google = screen.getByRole("link", { name: "Open in Google Maps" });
    expect(google).toHaveAttribute(
      "href",
      "https://www.google.com/maps/dir/?api=1&destination=39.427863%2C-77.419265",
    );
    expect(google).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Open in Apple Maps" })).toHaveAttribute(
      "href",
      "https://maps.apple.com/?daddr=39.427863%2C-77.419265",
    );
  });
});
