import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright smoke suite.
 *
 * The default run only exercises the sign-in route and proxy redirect —
 * no real Supabase credentials required. A credentialed end-to-end flow
 * (email sign-up, Google, Apple) runs in a separate manually triggered
 * workflow with secrets, documented in docs/development-roadmap.md.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["html", { open: "never" }], ["github"]] : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npx next dev -p 3000",
    url: "http://localhost:3000/sign-in",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
