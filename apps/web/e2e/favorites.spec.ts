import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { sessionCookie } from "./support/e2e-auth.mjs";
import { seedConnectedVehicle } from "./support/seed";

/**
 * Favorite stations (phase3_plan.md F8) against the provider stub: star a
 * station, find it on the Favorites tab, sign out and back in, and it's still
 * there; unstar it and it's gone.
 *
 * The stub has no AFDC "nearest" fixture, so the station comes from the Trip
 * Planner's along-route list (Frederick → Toronto, P3-F7's fixtures), which
 * caches it like any search does.
 */

test.beforeAll(() => {
  seedConnectedVehicle();
});

async function signIn(context: BrowserContext, baseURL: string) {
  const { name, value } = sessionCookie();
  await context.addCookies([{ name, value, url: baseURL }]);
}

test.beforeEach(async ({ context, baseURL }) => {
  await signIn(context, baseURL!);
});

async function pickPlace(page: Page, label: string, query: string) {
  const box = page.getByRole("combobox", { name: label });
  await box.fill(query);
  await page.getByRole("option").first().click();
}

/** Seeding keeps favorites, so start every attempt with none. */
async function clearFavorites(page: Page) {
  await page.goto("/charging?view=favorites");
  const empty = page.getByText("No favorite stations yet");
  // The list streams in after load: wait for it (or the empty state) before counting.
  await expect(empty.or(page.getByRole("region", { name: "Favorite stations" }))).toBeVisible();
  const stars = page.getByRole("button", { name: /^Remove .* from favorites$/ });
  for (let n = await stars.count(); n > 0; n = await stars.count()) {
    await stars.first().click();
    await expect(stars).toHaveCount(n - 1);
  }
  await expect(empty).toBeVisible();
}

test("star a station, keep it across sign-out, then unstar it", async ({
  page,
  context,
  baseURL,
}) => {
  await clearFavorites(page);

  // Find a station through the Trip Planner (cached by the along-route search).
  await page.goto("/trips");
  await pickPlace(page, "From", "Tesla Supercharger Frederick");
  await pickPlace(page, "To", "Toronto");
  const list = page.getByRole("region", { name: "Chargers along the route" });
  const card = list.getByRole("article").first();
  await expect(card).toBeVisible();
  const name = (await card.getAttribute("aria-label"))!;
  await card.getByRole("link", { name }).click();

  // Star it on the station page.
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await page.getByRole("button", { name: `Add ${name} to favorites` }).click();
  const starred = page.getByRole("button", { name: `Remove ${name} from favorites` });
  await expect(starred).toHaveAttribute("aria-pressed", "true");
  // Saved, not just flipped locally: a reload still shows it starred.
  await expect(starred).toBeEnabled();
  await page.reload();
  await expect(starred).toHaveAttribute("aria-pressed", "true");

  // It's on the Favorites tab.
  await page.goto("/charging");
  await page
    .getByRole("navigation", { name: "Charging views" })
    .getByRole("link", { name: "Favorites" })
    .click();
  await expect(page).toHaveURL(/[?&]view=favorites/);
  const favorites = page.getByRole("region", { name: "Favorite stations" });
  await expect(favorites.getByRole("article", { name })).toBeVisible();
  await expect(favorites.getByRole("status")).toHaveText("1 favorite station");

  // Sign out: the page needs a session again.
  await context.clearCookies();
  await page.goto("/charging?view=favorites");
  await expect(page).toHaveURL(/\/sign-in/);

  // Sign back in: still a favorite.
  await signIn(context, baseURL!);
  await page.goto("/charging?view=favorites");
  await expect(favorites.getByRole("article", { name })).toBeVisible();

  // Unstar it from the list; it drops out.
  await favorites.getByRole("button", { name: `Remove ${name} from favorites` }).click();
  await expect(page.getByText("No favorite stations yet")).toBeVisible();
  await page.reload();
  await expect(page.getByText("No favorite stations yet")).toBeVisible();
});
