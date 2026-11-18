/**
 * Test-only auth for the Playwright suite. Nothing here is a real secret.
 *
 * The suite runs without a Supabase project. Instead:
 * - `auth-stub.mjs` answers the one Supabase Auth call the app makes on the
 *   server (`GET /auth/v1/user`) by checking an HS256 token signed with
 *   `E2E_JWT_SECRET`;
 * - the API runs with `SUPABASE_JWT_SECRET=E2E_JWT_SECRET` (its HS256
 *   fallback), so it accepts the same token;
 * - tests put a Supabase-shaped session cookie in the browser
 *   (`sessionCookie()`), exactly what `@supabase/ssr` would have written after
 *   a real sign-in.
 *
 * Real sign-in flows are covered by the manual credentialed workflow.
 */
import { createHmac } from "node:crypto";

export const E2E_JWT_SECRET = "e2e-only-jwt-secret-not-a-real-key-0000000000";
/**
 * Reads a port from the environment, falling back to `fallback`.
 *
 * Parallel agent sessions each run Playwright from their own worktree. With
 * fixed ports and `reuseExistingServer`, a second run would silently test the
 * first run's servers, so each session sets its own ports (docs/plan.md §2.4).
 */
export function envPort(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${name} must be a port number, got ${JSON.stringify(raw)}`);
  }
  return port;
}

export const AUTH_STUB_PORT = envPort("E2E_AUTH_PORT", 54399);
export const AUTH_STUB_URL = `http://127.0.0.1:${AUTH_STUB_PORT}`;

// Must match services/api/scripts/e2e_backend.py.
export const E2E_USER = {
  id: "e2e00000-0000-4000-8000-000000000001",
  email: "e2e-driver@example.test",
};

const b64url = (input) => Buffer.from(input).toString("base64url");

export function signJwt(claims, secret = E2E_JWT_SECRET) {
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = b64url(JSON.stringify(claims));
  const signature = createHmac("sha256", secret)
    .update(`${header}.${payload}`)
    .digest("base64url");
  return `${header}.${payload}.${signature}`;
}

/** Returns the claims if `token` is a valid, unexpired E2E token, else null. */
export function verifyJwt(token, secret = E2E_JWT_SECRET) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const expected = createHmac("sha256", secret)
    .update(`${parts[0]}.${parts[1]}`)
    .digest("base64url");
  if (expected !== parts[2]) return null;
  try {
    const claims = JSON.parse(Buffer.from(parts[1], "base64url").toString());
    if (typeof claims.exp !== "number" || claims.exp * 1000 < Date.now()) return null;
    return claims;
  } catch {
    return null;
  }
}

export function supabaseUser(claims) {
  return {
    id: claims.sub,
    aud: "authenticated",
    role: "authenticated",
    email: claims.email,
    app_metadata: { provider: "email", providers: ["email"] },
    user_metadata: {},
    created_at: "2026-01-01T00:00:00Z",
  };
}

/**
 * The `sb-<ref>-auth-token` cookie `@supabase/ssr` reads, for a session that
 * stays valid for an hour (no refresh call during a test run).
 */
export function sessionCookie() {
  const now = Math.floor(Date.now() / 1000);
  const expiresIn = 3600;
  const claims = {
    sub: E2E_USER.id,
    email: E2E_USER.email,
    aud: "authenticated",
    role: "authenticated",
    iat: now,
    exp: now + expiresIn,
  };
  const session = {
    access_token: signJwt(claims),
    token_type: "bearer",
    expires_in: expiresIn,
    expires_at: now + expiresIn,
    refresh_token: "e2e-refresh-token",
    user: supabaseUser(claims),
  };
  // Storage key rule from supabase-js: sb-<first hostname label>-auth-token.
  const ref = new URL(AUTH_STUB_URL).hostname.split(".")[0];
  const value = `base64-${b64url(JSON.stringify(session))}`;
  // @supabase/ssr splits values over 3180 chars into .0/.1 cookies; ours is
  // far smaller, so one cookie is enough.
  if (value.length > 3180) throw new Error("E2E session cookie would be chunked");
  return { name: `sb-${ref}-auth-token`, value };
}
