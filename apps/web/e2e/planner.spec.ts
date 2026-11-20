import { expect, test, type Page } from "@playwright/test";

import { sessionCookie } from "./support/e2e-auth.mjs";
import { seedConnectedVehicle } from "./support/seed";

/**
 * Planner UI (phase4_plan F6) against the provider stub: Frederick → Toronto
 * route A (the real Mapbox and AFDC captures) planned through the real
 * `POST /routes/plan`. The seeded vehicle is the 2026 bZ XLE AWD, whose specs
 * come from the catalog. The map has no token in E2E, so the stops are
 * asserted in the list (the same labels drive the map markers).
 */

test.beforeEach(async ({ context, baseURL }) => {
  const { name, value } = sessionCookie();
  await context.addCookies([{ name, value, url: baseURL! }]);
});

async function pickPlace(page: Page, label: string, query: string) {
  const box = page.getByRole("combobox", { name: label });
  await box.fill(query);
  await page.getByRole("option").first().click();
}

async function openFrederickToronto(page: Page) {
  await page.goto("/trips");
  await pickPlace(page, "From", "Tesla Supercharger Frederick");
  await pickPlace(page, "To", "Toronto");
  await expect(page.getByRole("list", { name: "Routes" }).getByRole("button")).toHaveCount(2);
  return page.getByTestId("plan-panel");
}

test.describe("with a fresh battery reading", () => {
  test.beforeAll(() => {
    seedConnectedVehicle();
  });

  test("plans charging stops, saves the trip and opens it from Saved trips", async ({ page }) => {
    const panel = await openFrederickToronto(page);
    await expect(page.getByTestId("next-release-note")).not.toBeAttached();
    await expect(panel.getByRole("heading", { name: "Charging plan for Route A" })).toBeVisible();

    await panel.getByRole("button", { name: "Plan charging stops" }).click();
    const result = panel.getByTestId("plan-result");
    await expect(result).toHaveAttribute("data-status", "feasible", { timeout: 30_000 });

    // Each stop: arrival → departure charge, charging time, detour estimate.
    const stops = result.getByTestId("plan-stop");
    expect(await stops.count()).toBeGreaterThan(0);
    for (const stop of await stops.all()) {
      await expect(stop.getByTestId("plan-stop-soc")).toHaveText(/^\d+% → \d+%$/);
      await expect(stop).toContainText(/charge \d+/);
      await expect(stop).toContainText(/detour \d+ min \(estimate\)/);
    }
    const totals = result.getByTestId("plan-totals");
    await expect(totals).toContainText("Total time");
    await expect(totals).toContainText("Detours (estimate)");
    await expect(result.getByTestId("plan-starting-soc")).toContainText("64%");
    await expect(result.getByTestId("plan-starting-soc")).toContainText("from your car");

    await result.getByTestId("plan-assumptions").locator("summary").click();
    await expect(result.getByTestId("plan-assumptions")).toContainText(
      "derived from published figures, not measured",
    );

    await result.getByRole("button", { name: "Save trip" }).click();
    await expect(result.getByRole("status")).toContainText("Saved.");

    await page.getByRole("link", { name: "Saved trips" }).click();
    await expect(page).toHaveURL(/\/trips\/saved$/);
    await page.getByRole("link", { name: /Tesla Supercharger → Toronto/ }).first().click();
    await expect(page).toHaveURL(/\/trips\/[0-9a-f-]{36}$/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Toronto");
  });

  test("re-planning with a tight charge window shows a prominent infeasible state", async ({
    page,
  }) => {
    const panel = await openFrederickToronto(page);
    await panel.getByRole("button", { name: "Plan charging stops" }).click();
    await expect(panel.getByTestId("plan-result")).toHaveAttribute("data-status", "feasible", {
      timeout: 30_000,
    });

    await panel.getByText("Re-plan with different preferences").click();
    const form = panel.getByRole("form", { name: "Re-plan with different preferences" });
    await form.getByLabel("Reserve (%)").fill("50");
    await form.getByLabel("Charge to (%)").fill("55");
    await form.getByRole("button", { name: "Re-plan" }).click();

    const alert = panel.getByTestId("plan-infeasible");
    await expect(alert).toBeVisible({ timeout: 30_000 });
    await expect(alert).toHaveAttribute("role", "alert");
    await expect(alert).toContainText("Not feasible");
    await expect(panel.getByTestId("plan-result")).toHaveAttribute("data-status", "infeasible");
    // No route is presented as a valid plan: no stops, no totals.
    await expect(panel.getByTestId("plan-stop")).toHaveCount(0);
    await expect(panel.getByTestId("plan-totals")).toHaveCount(0);
  });
});

test.describe("with a stale battery reading", () => {
  test.beforeAll(() => {
    seedConnectedVehicle({ staleSoc: true });
  });

  test("asks for the starting charge and plans with the override", async ({ page }) => {
    const panel = await openFrederickToronto(page);
    await panel.getByRole("button", { name: "Plan charging stops" }).click();

    const prompt = panel.getByTestId("plan-soc-prompt");
    await expect(prompt).toBeVisible({ timeout: 30_000 });
    await expect(prompt).toContainText("too old to plan with");
    const input = prompt.getByLabel("Starting battery (%)");
    await expect(input).toHaveValue("64");

    await input.fill("90");
    await prompt.getByRole("button", { name: "Plan with this charge" }).click();
    const result = panel.getByTestId("plan-result");
    await expect(result).toHaveAttribute("data-status", "feasible", { timeout: 30_000 });
    await expect(result.getByTestId("plan-starting-soc")).toHaveText(
      "Starting charge 90% (entered by you)",
    );
  });
});
