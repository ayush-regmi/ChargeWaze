import { execFileSync } from "node:child_process";

import { test as setup } from "@playwright/test";

import { API_DIR, API_ENV, API_URL } from "./support/e2e-servers";

/**
 * Give the E2E user one connected Toyota, then deliver a signed
 * `VEHICLE_STATE` webhook through the running API so the Dashboard is fed by
 * the real ingestor. Runs before the browser tests (see playwright.config.ts).
 */
setup("seed a connected vehicle with telemetry", () => {
  execFileSync("uv", ["run", "python", "scripts/e2e_backend.py", "seed", "--api-url", API_URL], {
    cwd: API_DIR,
    env: { ...process.env, ...API_ENV },
    stdio: "inherit",
  });
});
