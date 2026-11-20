import { expect, test, type BrowserContext } from "@playwright/test";

import { sessionCookie } from "./support/e2e-auth.mjs";
import { seedConnectedVehicle } from "./support/seed";

/**
 * Profile privacy and account deletion (P6-F5, api §3.1b–d): accept the
 * privacy notice, delete the account with the typed confirmation, land on
 * sign-in, and a still-valid token can't bring the account back.
 *
 * The Auth stub has no admin API, and the API runs with SUPABASE_URL blank,
 * so the auth-user deletion stays owed in the E2E database (covered by
 * pytest). `afterAll` reseeds, which also clears the deletion tombstone, so
 * later specs get the E2E user back.
 */

test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  seedConnectedVehicle();
});

test.afterAll(() => {
  seedConnectedVehicle();
});

async function signIn(context: BrowserContext, baseURL: string) {
  const { name, value } = sessionCookie();
  await context.addCookies([{ name, value, url: baseURL }]);
}

test("accept the privacy notice, then delete the account", async ({ page, context, baseURL }) => {
  await signIn(context, baseURL!);
  await page.goto("/profile");

  const privacy = page.getByRole("region", { name: "Privacy" });
  await expect(privacy).toContainText("kept for 2 years");
  await expect(privacy.getByText("Not accepted")).toBeVisible();
  await privacy.getByRole("button", { name: "Accept" }).click();
  await expect(privacy.getByText("Accepted", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("region", { name: "Privacy" }).getByText("Accepted", { exact: true })).toBeVisible();

  // Delete: the confirm button needs the typed word.
  await page
    .getByRole("region", { name: "Delete account" })
    .getByRole("button", { name: "Delete account" })
    .click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Delete your account?");
  const confirm = dialog.getByRole("button", { name: "Delete account" });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel("Type DELETE to confirm").fill("DELETE");
  await confirm.click();

  await expect(page).toHaveURL(/\/sign-in/);

  // Signed out: the app needs a session again.
  await page.goto("/garage");
  await expect(page).toHaveURL(/\/sign-in/);
});

test("a still-valid token can't recreate the deleted account", async ({
  page,
  context,
  baseURL,
}) => {
  await signIn(context, baseURL!);
  await page.goto("/profile");
  await expect(page.getByText(/This account has been deleted/)).toBeVisible();

  // The car went with the account.
  await page.goto("/garage");
  await expect(page.getByText("No vehicles yet")).toBeVisible();
  await expect(page.getByRole("link", { name: /2026 TOYOTA bZ XLE AWD/ })).toHaveCount(0);
});
