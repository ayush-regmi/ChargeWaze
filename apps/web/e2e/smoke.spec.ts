import { expect, test } from "@playwright/test";

test.describe("signed-out smoke", () => {
  test("/profile redirects to /sign-in", async ({ page }) => {
    const response = await page.goto("/profile");
    // The proxy issues a 3xx to /sign-in.
    expect(page.url()).toMatch(/\/sign-in/);
    expect(response?.status()).toBeLessThan(500);
    await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /^sign in$/i })).toBeVisible();
  });

  test("sign-in page shows the Google button when enabled", async ({ page }) => {
    await page.goto("/sign-in");
    await expect(
      page.getByRole("button", { name: /continue with google/i }),
    ).toBeVisible();
  });
});
