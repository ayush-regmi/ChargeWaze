import { expect, test } from "@playwright/test";

import { sessionCookie } from "./support/e2e-auth.mjs";
import { seedConnectedVehicle } from "./support/seed";
import {
  apiAs,
  DAY,
  deliver,
  MINUTE,
  todayNoonUtc,
  type Reading,
} from "./telemetry-webhooks";

/**
 * Planned vs actual (phase5_plan F7 panel, F4 matching; P5-F8 close-out) on a
 * plan the real pipeline matched:
 *
 * 1. `POST /routes/plan` Frederick → Toronto against the provider stub, with
 *    a departure two days ago (matching looks ±2 h around `departure_time`;
 *    a plan made "now" could only match a drive that hasn't happened yet).
 * 2. Signed `VEHICLE_STATE` webhooks drive that plan: one leg per planned
 *    leg, a charging session at each stop, a reading every 15 minutes, and
 *    the planned distance in total. The post-ingest hook infers the legs and
 *    matches them to the plan (ADR 0009). No rows are written directly.
 *    The readings arrive two days late, after the seed's newer reading, which
 *    is the P5-F3 truncation case (`test_phase5_closeout.py`, xfail); so the
 *    spec runs the documented catch-up, recompute with `full`, as
 *    `analytics.spec.ts` does. Drop it when that bug is fixed.
 * 3. The trip page shows the panel, leg by leg; Saved trips says Completed.
 */

type PlanStop = {
  distance_along_route_km: number;
  distance_from_route_km: number;
  arrival_soc: number;
  departure_soc: number;
  charging_minutes: number;
};

type Plan = {
  trip_id: string;
  status: string;
  route: { distance_km: number };
  stops: PlanStop[];
  arrival_soc: number;
  assumptions: { detour_road_factor?: number };
};

const ORIGIN = { lat: 39.4143, lon: -77.4105, label: "Frederick, MD" };
const DESTINATION = { lat: 43.6532, lon: -79.3832, label: "Toronto, ON" };
// The seed webhook reports 24 650.32 km now; this drive ends before that.
const SEED_ODOMETER_KM = 24_650;
const STEP_MINUTES = 15;
const KM_PER_STEP = 25; // 100 km/h

let tripId = "";
let legCount = 0;

/** Readings for driving `plan` from `departure`, stopping to charge at each stop. */
function drivePlan(plan: Plan, departure: number): Reading[] {
  const factor = plan.assumptions.detour_road_factor ?? 0;
  const stops = plan.stops;
  const along = [
    0,
    ...stops.map((s) => s.distance_along_route_km),
    plan.route.distance_km,
  ];
  // Each leg drives its stretch of route plus the detour into its stop.
  const legKm = along.slice(1).map((to, i) => {
    const detour = i < stops.length ? 2 * stops[i].distance_from_route_km * factor : 0;
    return to - along[i] + detour;
  });
  const total = legKm.reduce((a, b) => a + b, 0);

  const readings: Reading[] = [];
  let at = departure;
  let odometer = SEED_ODOMETER_KM - Math.ceil(total) - 50;
  legKm.forEach((km, i) => {
    const socStart = i === 0 ? 90 : Math.round(stops[i - 1].departure_soc * 100);
    const socEnd = Math.round(
      (i < stops.length ? stops[i].arrival_soc : plan.arrival_soc) * 100,
    );
    const steps = Math.max(2, Math.ceil(km / KM_PER_STEP));
    // At rest, not charging, then moving.
    readings.push({ at, odometerKm: odometer, socPercent: socStart, charging: false });
    for (let s = 1; s <= steps; s++) {
      readings.push({
        at: at + s * STEP_MINUTES * MINUTE,
        odometerKm: Math.round((odometer + (km * s) / steps) * 100) / 100,
        socPercent: Math.round(socStart + ((socEnd - socStart) * s) / steps),
      });
    }
    at += steps * STEP_MINUTES * MINUTE;
    odometer = Math.round((odometer + km) * 100) / 100;
    if (i < stops.length) {
      // Plug in, charge, unplug: the next leg starts at the unplug reading.
      readings.push({ at: at + 5 * MINUTE, socPercent: socEnd, charging: true });
      at += (5 + Math.max(stops[i].charging_minutes, 10)) * MINUTE;
    } else {
      // Parked long enough to close the last leg.
      readings.push({ at: at + 40 * MINUTE, odometerKm: odometer, socPercent: socEnd });
    }
  });
  return readings;
}

/** formatKwh / formatSignedKwh in components/trips/observation-format.ts. */
function kwh(value: number): string {
  return `${value.toFixed(1)} kWh`;
}

function signedKwh(value: number): string {
  const r = Math.round(value * 100) / 100;
  if (r === 0) return "0.00 kWh";
  return `${r > 0 ? "+" : "−"}${Math.abs(r).toFixed(2)} kWh`;
}

test.beforeEach(async ({ context, baseURL }) => {
  const { name, value } = sessionCookie();
  await context.addCookies([{ name, value, url: baseURL! }]);
});

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({}, testInfo) => {
  // A plan, ~50 webhooks and the match: more than the default 30 s.
  testInfo.setTimeout(120_000);
  seedConnectedVehicle();
  const { items } = await apiAs("/api/v1/vehicles");
  const vehicleId: string = items[0].id;

  // 08:00 UTC two days ago.
  const departure = todayNoonUtc() - 2 * DAY - 4 * 60 * MINUTE;
  const plan: Plan = await apiAs("/api/v1/routes/plan", {
    method: "POST",
    body: JSON.stringify({
      vehicle_id: vehicleId,
      origin: ORIGIN,
      destination: DESTINATION,
      departure_time: new Date(departure).toISOString(),
      starting_soc_override: 0.9,
    }),
  });
  expect(plan.status).toBe("feasible");
  expect(plan.stops.length).toBeGreaterThan(0);
  tripId = plan.trip_id;
  legCount = plan.stops.length + 1;
  await apiAs(`/api/v1/trips/${tripId}/save`, { method: "POST" });

  for (const r of drivePlan(plan, departure + 2 * MINUTE)) await deliver(r);

  // Background hooks may still be running; repeat the catch-up until matched.
  await expect
    .poll(
      async () => {
        await apiAs("/api/v1/observed-trips/recompute", {
          method: "POST",
          body: JSON.stringify({ vehicle_id: vehicleId, full: true }),
        });
        const trip = await apiAs(`/api/v1/trips/${tripId}`);
        return {
          status: trip.status,
          legs: trip.observation?.legs.length ?? 0,
          quality: trip.observation?.legs.map(
            (l: { quality_flag: string }) => l.quality_flag,
          ),
        };
      },
      { timeout: 60_000 },
    )
    .toEqual({
      status: "completed",
      legs: legCount,
      quality: Array(legCount).fill("high"),
    });
});

test("the trip page compares the plan with the drives that carried it out", async ({
  page,
}) => {
  const { observation } = await apiAs(`/api/v1/trips/${tripId}`);
  expect(observation.observed_energy_kwh).not.toBeNull();

  await page.goto(`/trips/${tripId}`);
  const panel = page.getByTestId("observation-panel");
  await expect(
    panel.getByRole("heading", { name: "Planned vs actual" }),
  ).toBeVisible();
  await expect(panel).toContainText(`${legCount} drives matched`);

  await expect(panel.getByTestId("obs-energy")).toHaveText(
    [
      "Energy",
      kwh(observation.predicted_energy_kwh),
      kwh(observation.observed_energy_kwh),
      signedKwh(observation.error_kwh),
    ].join(""),
  );
  await expect(panel.getByTestId("obs-start-soc")).toContainText("90%");
  await expect(panel.getByTestId("obs-arrival-soc")).toContainText(/pts$/);
  await expect(panel.getByTestId("energy-missing-note")).toHaveCount(0);

  // One observed drive per planned leg, each paired with its leg.
  await expect(panel.getByTestId("legs-unpaired")).toHaveCount(0);
  const legs = panel.getByTestId("observation-leg");
  await expect(legs).toHaveCount(legCount);
  for (let i = 0; i < legCount; i++) {
    const leg = legs.nth(i);
    await expect(leg).toContainText(`Leg ${i + 1}`);
    await expect(leg.getByTestId("quality-badge")).toHaveText("High quality");
    await expect(leg.getByTestId("leg-soc")).toContainText(/planned · \d+% → \d+% actual/);
    await expect(leg).toContainText(kwh(observation.legs[i].energy_kwh));
  }
});

test("Saved trips marks the matched plan completed", async ({ page }) => {
  await page.goto("/trips/saved");
  const row = page.locator(`a[href="/trips/${tripId}"]`);
  await expect(row).toContainText("Completed");
});
