import { execFileSync } from "node:child_process";

import { API_DIR, API_ENV, API_URL } from "./e2e-servers";

/**
 * Reset the E2E user to one connected Toyota (not primary), then deliver a
 * signed `VEHICLE_STATE` webhook through the running API so the Dashboard is
 * fed by the real ingestor. Call from `beforeAll` so every attempt of a
 * serial group, including CI retries, starts from the same state.
 */
export function seedConnectedVehicle(): void {
  execFileSync("uv", ["run", "python", "scripts/e2e_backend.py", "seed", "--api-url", API_URL], {
    cwd: API_DIR,
    env: { ...process.env, ...API_ENV },
    stdio: "inherit",
  });
}
