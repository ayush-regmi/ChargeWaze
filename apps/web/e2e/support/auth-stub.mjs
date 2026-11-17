/**
 * Minimal stand-in for Supabase Auth, for the Playwright suite only.
 *
 * Serves `GET /auth/v1/user` (what `supabase.auth.getUser()` calls from
 * `proxy.ts`) and `GET /health` (Playwright's readiness probe). Every other
 * path is a 404, so an unexpected Auth call fails loudly instead of passing.
 */
import { createServer } from "node:http";

import { AUTH_STUB_PORT, supabaseUser, verifyJwt } from "./e2e-auth.mjs";

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

const server = createServer((req, res) => {
  const { pathname } = new URL(req.url ?? "/", "http://stub");

  if (req.method === "GET" && pathname === "/health") {
    return send(res, 200, { status: "ok" });
  }

  if (req.method === "GET" && pathname === "/auth/v1/user") {
    const auth = req.headers.authorization ?? "";
    const token = auth.startsWith("Bearer ") ? auth.slice("Bearer ".length) : "";
    const claims = token ? verifyJwt(token) : null;
    if (!claims) {
      return send(res, 401, {
        code: 401,
        error_code: "bad_jwt",
        msg: "invalid JWT",
      });
    }
    return send(res, 200, supabaseUser(claims));
  }

  console.error(`[auth-stub] unexpected ${req.method} ${pathname}`);
  return send(res, 404, { code: 404, msg: "not stubbed" });
});

server.listen(AUTH_STUB_PORT, "127.0.0.1", () => {
  console.log(`[auth-stub] listening on 127.0.0.1:${AUTH_STUB_PORT}`);
});
