import { readFile } from "node:fs/promises";

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
 * The readings are days old when they arrive, one webhook each, after the
 * seed's newer reading: the per-webhook inference must still build each
 * drive whole (the P5-F3 backlog fix), with no `recompute` call.
 *
 * Since P5-F4 every finished leg with energy has a backtest, so the
 * consumption lines and the estimated-vs-observed scatter must draw (P5-F8).
 * The webhook and API helpers are in `telemetry-webhooks.ts`.
 */

/** A drive: one reading every `stepMinutes`, then a parked reading so the leg closes. */
function drive(
  start: number,
  odometerKm: number,
  socs: number[],
  kmPerStep: number,
  stepMinutes: number,
): Reading[] {
  const readings: Reading[] = socs.map((soc, i) => ({
    at: start + i * stepMinutes * MINUTE,
    odometerKm: odometerKm + i * kmPerStep,
    socPercent: soc,
    charging: false,
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
  const today = todayNoonUtc();
  const readings = [
    ...drive(today - 10 * DAY, 24_600, [62, 60], 8, 30),
    ...drive(today - 3 * DAY, 24_608, [80, 78, 74, 70], 13, 10),
  ];
  for (const r of readings) await deliver(r);

  const { items } = await apiAs("/api/v1/vehicles");
  const vehicleId: string = items[0].id;
  // The post-ingest hooks run after each response; wait until both legs are in.
  await expect
    .poll(
      async () => {
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
  // Estimates come from P5-F4's leg backtests, so both comparisons draw.
  await expect(
    page.getByRole("img", { name: /Observed and estimated consumption per day/ }),
  ).toBeVisible();
  await expect(
    page.getByText("No consumption to compare in this range yet."),
  ).toHaveCount(0);
  const scatter = page.getByRole("img", {
    name: "Estimated vs observed energy per drive, in kWh",
  });
  await expect(scatter).toBeVisible();
  // Both drives have energy; the low-quality one is still plotted (hollow).
  await expect(scatter.locator("circle")).toHaveCount(2);
  await expect(scatter.locator('circle[data-hollow="true"]')).toHaveCount(1);

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
    /^chargewaze-trips-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}\.csv$/,
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
