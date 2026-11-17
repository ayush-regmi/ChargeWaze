import { expect, test } from "@playwright/test";

import { sessionCookie } from "./support/e2e-auth.mjs";
import { seedConnectedVehicle } from "./support/seed";

/**
 * Garage → Dashboard with seeded data (phase2_plan.md F8). The seed step gives
 * the E2E user a connected 2026 Toyota bZ and a webhook carrying 64% SoC,
 * 241.5 km range and "charging"; no odometer reading.
 */
// Both tests share the seeded vehicle and the second changes its primary flag.
test.describe.configure({ mode: "serial" });

// Reseed per group attempt, not once per run: a retry restarts the whole
// serial group, and the second test has already made the vehicle primary.
test.beforeAll(() => {
  seedConnectedVehicle();
});

test.beforeEach(async ({ context, baseURL }) => {
  const { name, value } = sessionCookie();
  await context.addCookies([{ name, value, url: baseURL! }]);
});

test("Garage lists the connected vehicle and the Dashboard shows its state", async ({
  page,
}) => {
  await page.goto("/garage");
  await expect(page.getByRole("heading", { name: "My Garage" })).toBeVisible();
  const card = page.getByRole("link", { name: /2026 TOYOTA bZ XLE AWD/ });
  await expect(card).toBeVisible();
  // The only vehicle is not primary yet; the Dashboard falls back to it.
  await expect(page.getByRole("button", { name: /set as primary/i })).toBeVisible();

  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expectDashboardState(page);

  // Served from the cached latest_state, so a reload shows the same values.
  await page.reload();
  await expectDashboardState(page);
});

test("Dashboard follows a vehicle set as primary in Garage", async ({ page }) => {
  await page.goto("/garage");
  await page.getByRole("button", { name: /set as primary/i }).click();
  await expect(page.getByText("Primary", { exact: true })).toBeVisible();

  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "2026 TOYOTA bZ XLE AWD" })).toBeVisible();
});

async function expectDashboardState(page: import("@playwright/test").Page) {
  await expect(page.getByRole("heading", { name: "2026 TOYOTA bZ XLE AWD" })).toBeVisible();
  await expect(page.getByRole("paragraph").filter({ hasText: /^Last update/ })).toBeVisible();
  await expect(page.getByTestId("stale-banner")).toHaveCount(0);
  await expect(page.getByTestId("awaiting-first-update")).toHaveCount(0);

  const battery = page.getByRole("region", { name: "Battery" });
  await expect(battery).toContainText("64");
  await expect(battery).toContainText("%");
  await expect(battery).toContainText("As of");

  // Default units are imperial: 241.5 km ≈ 150 mi.
  const range = page.getByRole("region", { name: "Range" });
  await expect(range).toContainText("150");
  await expect(range).toContainText("mi");

  // The bZ fixture has no odometer reading.
  await expect(page.getByRole("region", { name: "Odometer" })).toContainText("No reading yet");
  const charging = page.getByRole("region", { name: "Charging" });
  await expect(charging).toContainText("Charging");
  await expect(charging).not.toContainText("Not charging");
  await expect(charging).not.toContainText("Not reported");
}
