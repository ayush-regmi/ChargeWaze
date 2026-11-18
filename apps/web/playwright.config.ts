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
 * Specs that need data seed it themselves in `beforeAll` (see
 * `e2e/support/seed.ts`), so retries start from a clean state.
 *
 * Ports default to 3100 (web), 8765 (API) and 54399 (Auth stub); set
 * `E2E_WEB_PORT`, `E2E_API_PORT`, `E2E_AUTH_PORT` and `E2E_DATABASE_URL` to
 * run beside another session's suite (docs/plan.md §2.4).
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
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
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
