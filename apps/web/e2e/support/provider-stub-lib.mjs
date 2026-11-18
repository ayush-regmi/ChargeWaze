/**
 * Provider stub library: constants, fixture loading and matching, and the
 * server. The entry point is `provider-stub.mjs`; this file has no
 * `import.meta`, so `playwright.config.ts` (which Playwright compiles to
 * CommonJS) can import its constants.
 *
 * Stand-in for Mapbox and AFDC, for the Playwright suite only (phase3_plan.md
 * D16). The E2E API points `MAPBOX_API_BASE_URL` at `<stub>/mapbox` and
 * `AFDC_API_BASE_URL` at `<stub>/afdc` (see `e2e-servers.ts`), so the real
 * adapters run unchanged against recorded responses.
 *
 * Every stubbed request is a JSON file in `provider-fixtures/`. Adding a file
 * is enough to stub a new request; this file never changes for that. Files are
 * tried in filename order and the first match wins. A request no fixture
 * matches gets a 404 and a log line, so a missing fixture fails loudly.
 *
 * Fixture shape (all `match` fields optional except `path`/`pathPrefix`):
 *
 *   {
 *     "description": "What this is and where the data came from.",
 *     "match": {
 *       "method": "GET",                       // default GET
 *       "path": "/mapbox/…",                   // exact path, or
 *       "pathPrefix": "/mapbox/…",             // path prefix
 *       "query": { "q": "toronto" },           // each param (query string or
 *                                              // form body) contains the text,
 *                                              // case-insensitive
 *       "waypointsNear": {                     // Directions: the `lon,lat;lon,lat`
 *         "points": [{ "lat": 0, "lon": 0 }],  // path segment has these points
 *         "km": 25                             // in order, each within `km`
 *       },
 *       "routeNear": { "lat": 0, "lon": 0, "km": 10 }  // AFDC nearby-route: the
 *                                              // `route` WKT passes within `km`
 *     },
 *     "response": {
 *       "status": 200,                         // default 200
 *       "body": { … },                         // inline JSON, or
 *       "bodyFile": "data/x.json",             // path relative to the fixture
 *       "paginate": "afdc"                     // slice `fuel_stations` by the
 *                                              // request's `offset`/`limit`
 *     }
 *   }
 *
 * Requests must carry the E2E test credentials below, so a misconfigured run
 * can't pass with a real key. Nothing here is a real secret.
 */
import { readFileSync, readdirSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";

import { AUTH_STUB_PORT, envPort } from "./e2e-auth.mjs";

export const E2E_MAPBOX_TOKEN = "e2e-only-mapbox-token";
export const E2E_AFDC_API_KEY = "e2e-only-afdc-key";

/** Defaults to the Auth stub's port + 1, so the per-session +100 scheme carries over. */
export const PROVIDER_STUB_PORT = envPort("E2E_PROVIDER_PORT", AUTH_STUB_PORT + 1);
export const PROVIDER_STUB_URL = `http://127.0.0.1:${PROVIDER_STUB_PORT}`;

const EARTH_RADIUS_KM = 6371.0088;

function haversineKm(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Distance from `p` to segment `a`–`b` in km (local equirectangular projection). */
function segmentDistanceKm(p, a, b) {
  const kx = Math.cos((p.lat * Math.PI) / 180) * 111.32;
  const ky = 110.574;
  const ax = (a.lon - p.lon) * kx;
  const ay = (a.lat - p.lat) * ky;
  const bx = (b.lon - p.lon) * kx;
  const by = (b.lat - p.lat) * ky;
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2));
  return Math.hypot(ax + t * dx, ay + t * dy);
}

/** `LINESTRING(lon lat, …)` → `[{lat, lon}]`, or null if it isn't one. */
export function parseLineString(wkt) {
  const m = /^\s*LINESTRING\s*\((.*)\)\s*$/i.exec(wkt ?? "");
  if (!m) return null;
  const points = m[1].split(",").map((pair) => {
    const [lon, lat] = pair.trim().split(/\s+/).map(Number);
    return { lat, lon };
  });
  return points.every((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon)) ? points : null;
}

/** The `lon,lat;lon,lat` waypoints at the end of a Directions path. */
export function parseWaypoints(pathname) {
  const last = decodeURIComponent(pathname.split("/").pop() ?? "");
  const points = last.split(";").map((pair) => {
    const [lon, lat] = pair.split(",").map(Number);
    return { lat, lon };
  });
  return points.every((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon)) ? points : null;
}

export function routePassesNear(line, point, km) {
  if (line.length === 1) return haversineKm(line[0], point) <= km;
  for (let i = 1; i < line.length; i++) {
    if (segmentDistanceKm(point, line[i - 1], line[i]) <= km) return true;
  }
  return false;
}

/**
 * Does `fixture` match the request? `params` holds the query string and, for
 * form posts, the form body.
 */
export function matches(fixture, { method, pathname, params }) {
  const m = fixture.match;
  if ((m.method ?? "GET").toUpperCase() !== method.toUpperCase()) return false;
  if (m.path !== undefined && pathname !== m.path) return false;
  if (m.pathPrefix !== undefined && !pathname.startsWith(m.pathPrefix)) return false;
  for (const [key, text] of Object.entries(m.query ?? {})) {
    const value = params.get(key);
    if (value === null || !value.toLowerCase().includes(String(text).toLowerCase())) return false;
  }
  if (m.waypointsNear) {
    const got = parseWaypoints(pathname);
    const want = m.waypointsNear.points;
    if (!got || got.length !== want.length) return false;
    if (!want.every((p, i) => haversineKm(p, got[i]) <= m.waypointsNear.km)) return false;
  }
  if (m.routeNear) {
    const line = parseLineString(params.get("route"));
    if (!line || !routePassesNear(line, m.routeNear, m.routeNear.km)) return false;
  }
  return true;
}

/** Load and check every fixture. Throws on a malformed file so the run fails at start. */
export function loadFixtures(dir) {
  return readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((file) => {
      const full = path.join(dir, file);
      const fixture = JSON.parse(readFileSync(full, "utf8"));
      const where = `provider fixture ${file}`;
      if (!fixture.match || (fixture.match.path === undefined && fixture.match.pathPrefix === undefined)) {
        throw new Error(`${where}: match.path or match.pathPrefix is required`);
      }
      const response = fixture.response ?? {};
      let body = response.body;
      if (response.bodyFile !== undefined) {
        body = JSON.parse(readFileSync(path.resolve(dir, response.bodyFile), "utf8"));
      }
      if (body === undefined) throw new Error(`${where}: response.body or response.bodyFile is required`);
      return { file, match: fixture.match, status: response.status ?? 200, body, paginate: response.paginate };
    });
}

/** AFDC pages with `offset`/`limit`; `total_results` stays the full count. */
export function paginateAfdc(body, params) {
  if (!Array.isArray(body.fuel_stations)) return body;
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  const rawLimit = Number(params.get("limit"));
  const limit = Number.isFinite(rawLimit) && rawLimit > 0 ? rawLimit : body.fuel_stations.length;
  return { ...body, fuel_stations: body.fuel_stations.slice(offset, offset + limit) };
}

/** Credentials check: the E2E values only, as the real providers would reject others. */
function credentialError(pathname, params) {
  if (pathname.startsWith("/mapbox/") && params.get("access_token") !== E2E_MAPBOX_TOKEN) {
    return { status: 401, body: { message: "Not Authorized - Invalid Token" } };
  }
  if (pathname.startsWith("/afdc/") && params.get("api_key") !== E2E_AFDC_API_KEY) {
    return { status: 403, body: { error: { code: "API_KEY_INVALID", message: "e2e stub: wrong key" } } };
  }
  return null;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

export function startProviderStub({ dir, port = PROVIDER_STUB_PORT }) {
  const fixtures = loadFixtures(dir);

  const server = createServer(async (req, res) => {
    const send = (status, body) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body));
    };
    const url = new URL(req.url ?? "/", "http://stub");
    const { pathname } = url;
    const method = req.method ?? "GET";

    if (method === "GET" && pathname === "/health") return send(200, { status: "ok" });

    const params = new URLSearchParams(url.searchParams);
    if ((req.headers["content-type"] ?? "").includes("application/x-www-form-urlencoded")) {
      for (const [k, v] of new URLSearchParams(await readBody(req))) params.append(k, v);
    }

    const denied = credentialError(pathname, params);
    if (denied) {
      console.error(`[provider-stub] bad credentials for ${method} ${pathname}`);
      return send(denied.status, denied.body);
    }

    const fixture = fixtures.find((f) => matches(f, { method, pathname, params }));
    if (!fixture) {
      // The path only: query strings carry the test credentials.
      console.error(`[provider-stub] no fixture for ${method} ${pathname}`);
      return send(404, { message: "e2e provider stub: no fixture matches this request" });
    }
    const body = fixture.paginate === "afdc" ? paginateAfdc(fixture.body, params) : fixture.body;
    return send(fixture.status, body);
  });

  server.listen(port, "127.0.0.1", () => {
    console.log(`[provider-stub] ${fixtures.length} fixtures, listening on 127.0.0.1:${port}`);
  });
  return server;
}
