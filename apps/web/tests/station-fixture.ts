import type { Station } from "@/lib/stations";

/** A station shaped like an api §3.10 item; override what a test cares about. */
export function makeStation(patch: Partial<Station> = {}): Station {
  return {
    id: "8f0c7f8e-2b8e-4a55-9c1b-7d1a0e6f3c21",
    provider: "afdc",
    external_id: "307447",
    name: "POTOMAC EDISON MAX KEHNE DC1",
    operator: null,
    network: "ChargePoint Network",
    street_address: "1108 W 7th St",
    city: "Frederick",
    region: "MD",
    postal_code: "21701",
    country: "US",
    location: { lat: 39.427863, lon: -77.419265 },
    connectors: [
      { type: "CCS1", count: 1, power_kw: 62.0 },
      { type: "CHAdeMO", count: 1, power_kw: 62.5 },
    ],
    max_power_kw: 62.5,
    access: "public",
    access_detail: null,
    restricted_access: false,
    status: "open",
    pricing_text: null,
    compatible: true,
    live_availability: null,
    source_updated_at: "2026-11-17T02:35:33Z",
    fetched_at: "2026-11-18T19:00:00Z",
    is_stale: false,
    ...patch,
  };
}
