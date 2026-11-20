import path from "node:path";

import { E2E_JWT_SECRET, envPort } from "./e2e-auth.mjs";
import { E2E_AFDC_API_KEY, E2E_MAPBOX_TOKEN, PROVIDER_STUB_URL } from "./provider-stub-lib.mjs";

/**
 * Ports and backend settings shared by `playwright.config.ts` and the seed
 * step. Ports differ from the usual dev ports (3000/8000) so a running dev
 * stack is never reused by mistake. `E2E_WEB_PORT` / `E2E_API_PORT` override
 * them so parallel sessions don't share servers (docs/plan.md §2.4).
 */
export const WEB_PORT = envPort("E2E_WEB_PORT", 3100);
export const API_PORT = envPort("E2E_API_PORT", 8765);
export const API_URL = `http://127.0.0.1:${API_PORT}`;
export const API_DIR = path.resolve(__dirname, "../../../../services/api");

export const API_ENV: Record<string, string> = {
  APP_ENV: "development",
  DATABASE_URL:
    process.env.E2E_DATABASE_URL ??
    "postgresql+psycopg://chargewise:chargewise@localhost:5432/chargewise_e2e",
  // Environment values win over services/api/.env, so a developer's real
  // settings there can't leak into the run.
  SUPABASE_JWKS_URL: "",
  SUPABASE_JWT_SECRET: E2E_JWT_SECRET,
  SUPABASE_JWT_AUDIENCE: "authenticated",
  // Account deletion (P6-F5) calls the Supabase admin API with these. Blank,
  // so a real service-role key in services/api/.env is never used: the
  // auth-user deletion just stays owed in the test database.
  SUPABASE_URL: "",
  SUPABASE_SERVICE_ROLE_KEY: "",
  SMARTCAR_CLIENT_ID: "",
  SMARTCAR_CLIENT_SECRET: "",
  SMARTCAR_STATE_SECRET: "e2e-only-state-secret",
  // Test-only HMAC key for signing the seeded webhook (not a real token).
  SMARTCAR_MANAGEMENT_TOKEN: "e2e-only-management-token",
  // Any accidental live Smartcar call fails fast instead of leaving the box.
  SMARTCAR_API_BASE_URL: "http://127.0.0.1:9/v3",
  SMARTCAR_AUTH_URL: "http://127.0.0.1:9/oauth2/token",
  // Mapbox and AFDC go to the fixture-backed provider stub
  // (provider-stub.mjs) with test-only credentials, so a real key in
  // services/api/.env is never used by the suite.
  MAPBOX_SECRET_TOKEN: E2E_MAPBOX_TOKEN,
  MAPBOX_API_BASE_URL: `${PROVIDER_STUB_URL}/mapbox`,
  AFDC_API_KEY: E2E_AFDC_API_KEY,
  AFDC_API_BASE_URL: `${PROVIDER_STUB_URL}/afdc`,
  SENTRY_DSN_BACKEND: "",
  ALLOWED_ORIGINS: `http://localhost:${WEB_PORT}`,
};
