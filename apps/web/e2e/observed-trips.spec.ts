import { createHmac, randomUUID } from "node:crypto";

import { expect, test, type Locator, type Page } from "@playwright/test";

import { sessionCookie } from "./support/e2e-auth.mjs";
import { API_ENV, API_URL } from "./support/e2e-servers";
import { seedConnectedVehicle } from "./support/seed";

/**
 * Observed drives (phase5_plan F7) fed by the real pipeline: signed
 * `VEHICLE_STATE` webhooks with odometer and SoC readings go through the API,
 * the post-ingest hook infers the legs (P5-F3), and the UI lists them for
 * review. The readings are sparse (key-off only), so both legs are
 * low-quality with a bounded start and land in "Needs review".
 *
 * The planned-vs-actual panel isn't covered here: `observation` stays null
 * until P5-F4's matching lands, so it's tested with fixtures in Vitest.
 */

// Must match services/api/scripts/e2e_backend.py.
const SMARTCAR_VEHICLE_ID = "e2e-smartcar-vehicle";
const SMARTCAR_USER_ID = "e2e-smartcar-user";

type Reading = { at: Date; odometerKm: number; socPercent: number };

function signal(code: string, body: Record<string, unknown>, at: Date) {
  const ms = at.getTime();
  return { code, name: code, group: code.split("-")[0], body, meta: { oemUpdatedAt: ms, retrievedAt: ms } };
}

async function sendReading(r: Reading): Promise<void> {
  const payload = {
    eventId: `e2e-observed-${randomUUID()}`,
    eventType: "VEHICLE_STATE",
    data: {
      user: { id: SMARTCAR_USER_ID },
      vehicle: { id: SMARTCAR_VEHICLE_ID, make: "TOYOTA", model: "bZ XLE AWD", year: 2026 },
      signals: [
        signal("odometer-traveleddistance", { unit: "km", value: r.odometerKm }, r.at),
        signal("tractionbattery-stateofcharge", { unit: "percent", value: r.socPercent }, r.at),
        signal("charge-ischarging", { value: false }, r.at),
      ],
      triggers: [{ type: "SIGNAL_UPDATED", signal: { code: "odometer-traveleddistance" } }],
    },
    meta: {
      deliveryId: `delivery-${randomUUID()}`,
      deliveredAt: Date.now(),
      webhookId: "e2e-webhook",
      version: "4.0",
      mode: "LIVE",
    },
  };
  const body = JSON.stringify(payload);
  const signature = createHmac("sha256", API_ENV.SMARTCAR_MANAGEMENT_TOKEN!)
    .update(body)
    .digest("hex");
  const res = await fetch(`${API_URL}/api/v1/webhooks/smartcar`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "SC-Signature": signature },
    body,
  });
  expect(res.ok, `webhook → HTTP ${res.status}`).toBe(true);
}

function hoursAgo(h: number): Date {
  return new Date(Date.now() - h * 3_600_000);
}

test.beforeEach(async ({ context, baseURL }) => {
  const { name, value } = sessionCookie();
  await context.addCookies([{ name, value, url: baseURL! }]);
});

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  seedConnectedVehicle({ staleSoc: true });
  // Two key-off-only drives, a day and a half ago and a day ago. Each pair of
  // readings is hours apart, so the start time is only known as a range.
  await sendReading({ at: hoursAgo(48), odometerKm: 24712.3, socPercent: 66 });
  await sendReading({ at: hoursAgo(33.5), odometerKm: 24740.9, socPercent: 61 });
  await sendReading({ at: hoursAgo(26), odometerKm: 24740.9, socPercent: 61 });
  await sendReading({ at: hoursAgo(24), odometerKm: 24770.9, socPercent: 58 });
});

async function openNeedsReview(page: Page): Promise<Locator> {
  await page.goto("/trips/saved");
  await page.getByRole("link", { name: "Observed drives" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Observed drives" })).toBeVisible();
  await page
    .getByRole("navigation", { name: "Filter observed drives" })
    .getByRole("link", { name: "Needs review" })
    .click();
  await expect(page).toHaveURL(/view=observed&review=needs_review/);
  return page.getByTestId("observed-drive");
}

test("lists inferred drives for review and confirms or rejects them", async ({ page }) => {
  // Inference runs as a background task after each webhook; reload until both legs exist.
  let drives = await openNeedsReview(page);
  await expect(async () => {
    await page.reload();
    await expect(drives).toHaveCount(2);
  }).toPass({ timeout: 20_000 });

  // Newest first. Each one: a start range, low quality with reasons, review buttons.
  for (const drive of await drives.all()) {
    await expect(drive.getByTestId("drive-times")).toContainText("started between");
    await expect(drive.getByTestId("quality-badge")).toHaveText("Low quality");
    await expect(drive.getByTestId("quality-reasons")).toContainText(
      "The exact start time isn't known",
    );
    await expect(drive.getByTestId("review-status")).toHaveText("Needs review");
  }
  const newest = drives.nth(0);
  const older = drives.nth(1);
  await expect(newest.getByTestId("drive-soc")).toHaveText("61% → 58%");
  await expect(older.getByTestId("drive-soc")).toHaveText("66% → 61%");
  const olderId = await older.getAttribute("data-id");
  const newestId = await newest.getAttribute("data-id");

  // Confirm the older drive: it leaves the Needs review filter.
  await older.getByRole("button", { name: /^Confirm / }).click();
  await expect(drives).toHaveCount(1);
  await expect(drives.first()).toHaveAttribute("data-id", newestId!);

  // Reject the newer one: the filter is empty.
  await drives.first().getByRole("button", { name: /^Reject / }).click();
  await expect(page.getByText("Nothing to review")).toBeVisible();

  // "All drives" keeps the confirmed drive and hides the rejected one (§3.18 default).
  await page
    .getByRole("navigation", { name: "Filter observed drives" })
    .getByRole("link", { name: "All drives" })
    .click();
  drives = page.getByTestId("observed-drive");
  await expect(drives).toHaveCount(1);
  await expect(drives.first()).toHaveAttribute("data-id", olderId!);
  await expect(drives.first().getByTestId("review-status")).toHaveText("Confirmed");
  await expect(drives.first().getByRole("button", { name: /^Confirm / })).toHaveCount(0);
  await expect(drives.first().getByRole("button", { name: /^Reject / })).toBeVisible();
});

test("Saved plans tab still lists saved trips", async ({ page }) => {
  await page.goto("/trips/saved?view=observed");
  await page.getByRole("navigation", { name: "Trip history" }).getByRole("link", { name: "Saved plans" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Saved trips" })).toBeVisible();
  await expect(page).toHaveURL(/\/trips\/saved$/);
});
