import { defineConfig, devices } from "@playwright/test";

import { AUTH_STUB_URL } from "./e2e/support/e2e-auth.mjs";
import { API_DIR, API_ENV, API_PORT, API_URL, WEB_PORT } from "./e2e/support/e2e-servers";

/**
 * Playwright E2E suite (no real Supabase or Smartcar).
 *
 * Starts three servers:
 * - an Auth stub standing in for Supabase Auth (`e2e/support/auth-stub.mjs`);
 * - the FastAPI backend on a local `chargewise_e2e` Postgres database, with
 *   test-only secrets and the Smartcar API pointed at an unreachable address;
 * - `next dev` pointed at both.
 *
 * The `setup` project seeds one connected vehicle and delivers a signed
 * webhook before the browser tests run.
 *
 * Needs Postgres on localhost:5432 (`docker compose up -d`) and `uv`. The
 * credentialed sign-in flows (email, Google, Apple) run in a separate manual
 * workflow.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["html", { open: "never" }], ["github"]] : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? `http://localhost:${WEB_PORT}`,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "setup",
      testMatch: /.*\.setup\.ts/,
    },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      dependencies: ["setup"],
    },
  ],
  webServer: [
    {
      name: "auth-stub",
      command: "node e2e/support/auth-stub.mjs",
      url: `${AUTH_STUB_URL}/health`,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
    },
    {
      name: "api",
      command: `uv run python scripts/e2e_backend.py prepare && uv run uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: API_DIR,
      url: `${API_URL}/health`,
      env: API_ENV,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      name: "web",
      command: `npx next dev -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/sign-in`,
      env: {
        NEXT_PUBLIC_SUPABASE_URL: AUTH_STUB_URL,
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "e2e-anon-key",
        NEXT_PUBLIC_API_BASE_URL: API_URL,
      },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
