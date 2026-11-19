import { createHmac, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

import { E2E_USER, sessionCookie, signJwt } from "./support/e2e-auth.mjs";
import { API_ENV, API_URL } from "./support/e2e-servers";
import { seedConnectedVehicle } from "./support/seed";

/**
 * Analytics (phase5_plan F6) on legs built by the real pipeline: the spec
 * signs `VEHICLE_STATE` webhooks (odometer, SoC, charging) the way Smartcar
 * would, the webhook's post-ingest hook runs trip inference (ADR 0009), and
 * the page reads `/analytics/consumption` and `/analytics/trips`. No rows are
 * written directly.
 *
 * Two drives, a week apart, both inside the default 30-day range:
 * - 10 days ago: 8 km on 2 SoC points (low quality, needs review);
 * - 3 days ago: 39 km on 10 SoC points, a reading every 10 minutes (high).
 *
 * The readings are days old when they arrive, one webhook each. The
 * per-webhook inference then closes the second drive after its first
 * reading (P5-F3 follow-up, see phase5_plan F6 "As built"), so the spec runs
 * the documented catch-up, `POST /observed-trips/recompute` with `full`,
 * as the API user before opening the page.
 */

// Must match services/api/scripts/e2e_backend.py.
const SMARTCAR_VEHICLE_ID = "e2e-smartcar-vehicle";
const SMARTCAR_USER_ID = "e2e-smartcar-user";
const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;

type Reading = { at: number; odometerKm: number; socPercent: number };

function signal(code: string, body: Record<string, unknown>, at: number) {
  return {
    code,
    name: code,
    group: code.split("-")[0],
    body,
    meta: { oemUpdatedAt: at, retrievedAt: at },
  };
}

/** POST one signed VEHICLE_STATE webhook carrying `r`. */
async function deliver(r: Reading) {
  const payload = {
    eventId: `e2e-analytics-${randomUUID()}`,
    eventType: "VEHICLE_STATE",
    data: {
      user: { id: SMARTCAR_USER_ID },
      vehicle: {
        id: SMARTCAR_VEHICLE_ID,
        make: "TOYOTA",
        model: "bZ XLE AWD",
        year: 2026,
      },
      signals: [
        signal(
          "odometer-traveleddistance",
          { unit: "km", value: r.odometerKm },
          r.at,
        ),
        signal(
          "tractionbattery-stateofcharge",
          { unit: "percent", value: r.socPercent },
          r.at,
        ),
        signal("charge-ischarging", { value: false }, r.at),
      ],
      triggers: [
        {
          type: "SIGNAL_UPDATED",
          signal: { code: "odometer-traveleddistance" },
        },
      ],
    },
    meta: {
      deliveryId: `delivery-${randomUUID()}`,
      deliveredAt: r.at,
      webhookId: "e2e",
      version: "4.0",
      mode: "LIVE",
    },
  };
  const body = JSON.stringify(payload);
  const signature = createHmac("sha256", API_ENV.SMARTCAR_MANAGEMENT_TOKEN)
    .update(body)
    .digest("hex");
  const response = await fetch(`${API_URL}/api/v1/webhooks/smartcar`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "SC-Signature": signature },
    body,
  });
  expect(response.status, await response.clone().text()).toBe(200);
}

/** Call the API as the E2E user (the same identity the browser session has). */
async function apiAs(path: string, init: RequestInit = {}) {
  const now = Math.floor(Date.now() / 1000);
  const token = signJwt({
    sub: E2E_USER.id,
    email: E2E_USER.email,
    aud: "authenticated",
    role: "authenticated",
    iat: now,
    exp: now + 600,
  });
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  });
  expect(response.status, await response.clone().text()).toBe(200);
  return response.json();
}

/** A drive: one reading every `stepMinutes`, then a parked reading so the leg closes. */
function drive(
  start: number,
  odometerKm: number,
  socs: number[],
  kmPerStep: number,
  stepMinutes: number,
): Reading[] {
  const readings = socs.map((soc, i) => ({
    at: start + i * stepMinutes * MINUTE,
    odometerKm: odometerKm + i * kmPerStep,
    socPercent: soc,
  }));
  const last = readings[readings.length - 1];
  return [...readings, { ...last, at: last.at + 30 * MINUTE }];
}

test.beforeEach(async ({ context, baseURL }) => {
  const { name, value } = sessionCookie();
  await context.addCookies([{ name, value, url: baseURL! }]);
});

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  seedConnectedVehicle();
  // Midday UTC, so the drives stay on one local day in any time zone near UTC.
  const today = Date.UTC(
    new Date().getUTCFullYear(),
    new Date().getUTCMonth(),
    new Date().getUTCDate(),
    12,
  );
  const readings = [
    ...drive(today - 10 * DAY, 24_600, [62, 60], 8, 30),
    ...drive(today - 3 * DAY, 24_608, [80, 78, 74, 70], 13, 10),
  ];
  for (const r of readings) await deliver(r);

  const { items } = await apiAs("/api/v1/vehicles");
  const vehicleId: string = items[0].id;
  // Background hooks may still be running; repeat the catch-up until both legs are right.
  await expect
    .poll(
      async () => {
        await apiAs("/api/v1/observed-trips/recompute", {
          method: "POST",
          body: JSON.stringify({ vehicle_id: vehicleId, full: true }),
        });
        const trips = await apiAs(
          `/api/v1/analytics/trips?vehicle_id=${vehicleId}`,
        );
        return trips.items.map(
          (t: { distance_km: number; quality_flag: string }) =>
            `${t.distance_km} ${t.quality_flag}`,
        );
      },
      { timeout: 30_000 },
    )
    .toEqual(["39 high", "8 low"]);
});

test("shows trends, quality badges and a CSV that matches the table", async ({
  page,
}) => {
  await page.goto("/analytics?group_by=day");
  // The browser's zone is added to the URL.
  await expect(page).toHaveURL(/tz=/);
  const table = page.getByTestId("analytics-trips-table");
  await expect(table.locator("tbody tr")).toHaveCount(2);

  // Per-trip quality badges, with the reasons as the badge's description.
  const low = table.locator('[data-quality="low"]');
  await expect(low).toHaveCount(1);
  await low.focus();
  await expect(
    page.getByRole("tooltip").filter({ visible: true }),
  ).toContainText("Used under 10 % of the battery");
  await expect(
    table.getByRole("link", { name: "Needs review" }),
  ).toHaveAttribute("href", "/trips/saved");
  await expect(table.getByText("Not in totals")).toHaveCount(1);

  // Daily buckets, then weekly.
  const distance = page.getByRole("table", { name: /Distance driven per day/ });
  await expect(distance.getByRole("rowheader").first()).toHaveText(
    /^\d{4}-\d{2}-\d{2}$/,
  );
  await expect(
    page.getByRole("img", { name: /Distance driven per day/ }),
  ).toBeVisible();
  // Estimates come from P5-F4's backtests; until a leg has one, the
  // consumption chart says there is nothing to compare instead of drawing.
  await expect(
    page
      .getByRole("img", { name: /Observed and estimated consumption per day/ })
      .or(page.getByText("No consumption to compare in this range yet.")),
  ).toBeVisible();

  await page.getByLabel("Group by").selectOption("week");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).not.toHaveURL(/group_by=day/);
  const weekly = page.getByRole("table", { name: /Distance driven per week/ });
  await expect(weekly.getByRole("rowheader").first()).toHaveText(
    /^\d{4}-W\d{2}$/,
  );
  // Only the counted drive (39 km) is in the totals; the needs-review one is not.
  await expect(page.getByTestId("analytics-summary")).toContainText("1 trip");

  // The CSV has the same drives, in the same order, as the table.
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download CSV" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(
    /^chargewise-trips-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}\.csv$/,
  );
  const csv = (await readFile((await download.path())!, "utf8"))
    .trim()
    .split(/\r?\n/);
  const header = csv[0].split(",");
  const rows = csv.slice(1).map((line) => {
    const cells = line.split(",");
    return Object.fromEntries(header.map((h, i) => [h, cells[i]]));
  });
  const tableRows = await table.locator("tbody tr").evaluateAll((trs) =>
    trs.map((tr) => ({
      id: tr.getAttribute("data-trip-id"),
      quality: tr.querySelector("[data-quality]")?.getAttribute("data-quality"),
    })),
  );
  expect(rows.map((r) => ({ id: r.id, quality: r.quality_flag }))).toEqual(
    tableRows,
  );
  expect(rows.map((r) => r.review_status).sort()).toEqual([
    "auto",
    "needs_review",
  ]);
});

test("shows the empty state for a range with no drives", async ({ page }) => {
  await page.goto("/analytics?from=2020-01-01&to=2020-01-31&tz=UTC");
  await expect(page.getByTestId("analytics-empty")).toContainText(
    "No trips yet. Trips appear here automatically after you drive.",
  );
});
