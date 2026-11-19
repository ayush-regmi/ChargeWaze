import { expect, test, type Page } from "@playwright/test";

import { sessionCookie } from "./support/e2e-auth.mjs";
import { seedConnectedVehicle } from "./support/seed";

/**
 * Trip Planner E1 (phase3_plan.md F7) against the provider stub: search both
 * endpoints, see both real Frederick → Toronto routes, and see the stations
 * along each. Counts come from the stub's fixtures
 * (`support/provider-fixtures/3*-afdc-nearby-route-*.json`): route A is the
 * real 122-station capture, route B the 83 of them within 3 km of route B.
 * Three stations in both files have `restricted_access: true`, which the
 * stations API leaves out by default (F5), so the page lists 119 and 80.
 */
const RESTRICTED_IN_FIXTURE = 3;
const ROUTE_A_STATIONS = 122 - RESTRICTED_IN_FIXTURE;
const ROUTE_B_STATIONS = 83 - RESTRICTED_IN_FIXTURE;

test.beforeAll(() => {
  seedConnectedVehicle();
});

test.beforeEach(async ({ context, baseURL }) => {
  const { name, value } = sessionCookie();
  await context.addCookies([{ name, value, url: baseURL! }]);
});

async function pickPlace(page: Page, label: string, query: string) {
  const box = page.getByRole("combobox", { name: label });
  await box.fill(query);
  await page.getByRole("option").first().click();
}

test("search a trip, compare routes and see the stations along each", async ({ page }) => {
  await page.goto("/trips");
  await expect(page.getByRole("heading", { name: "Trip Planner" })).toBeVisible();
  // The Phase 3 next-release note was removed by P4-F6 (charging plans ship).
  await expect(page.getByTestId("next-release-note")).not.toBeAttached();

  await pickPlace(page, "From", "Tesla Supercharger Frederick");
  await pickPlace(page, "To", "Toronto");

  const routes = page.getByRole("list", { name: "Routes" }).getByRole("button");
  await expect(routes).toHaveCount(2);
  const routeA = routes.filter({ hasText: "Route A" });
  const routeB = routes.filter({ hasText: "Route B" });
  await expect(routeA).toHaveAttribute("aria-pressed", "true");
  await expect(routeA).toContainText("Shortest");
  await expect(routeA).toContainText("US → CA");

  const count = page.getByTestId("route-station-count");
  await expect(count).toHaveText(new RegExp(`^${ROUTE_A_STATIONS} stations within .* of Route A`));
  const list = page.getByRole("region", { name: "Chargers along the route" });
  await expect(list.getByRole("article")).toHaveCount(ROUTE_A_STATIONS);
  // The other route's stations load in the background (D15).
  await expect(routeB.getByTestId("route-chargers")).toHaveText(/chargers? within/);

  await routeB.click();
  await expect(routeB).toHaveAttribute("aria-pressed", "true");
  await expect(count).toHaveText(new RegExp(`^${ROUTE_B_STATIONS} stations within .* of Route B`));
  await expect(list.getByRole("article")).toHaveCount(ROUTE_B_STATIONS);
  await expect(page).toHaveURL(/[?&]route=1(&|$)/);
  await expect(page).toHaveURL(/[?&]from_lat=39\.3847/);

  // The URL alone restores the plan.
  await page.reload();
  await expect(page.getByRole("combobox", { name: "From" })).toHaveValue("Tesla Supercharger");
  await expect(page.getByRole("combobox", { name: "To" })).toHaveValue("Toronto");
  await expect(
    page.getByRole("list", { name: "Routes" }).getByRole("button", { name: /Route B/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("route-station-count")).toHaveText(
    new RegExp(`^${ROUTE_B_STATIONS} stations within .* of Route B`),
  );
});
