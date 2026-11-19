/**
 * Settings and privacy scrubbing shared by every Sentry runtime (browser,
 * Node.js server, edge). Mirrors `services/api/app/core/observability.py`.
 *
 * Sentry is off unless `NEXT_PUBLIC_SENTRY_DSN` is set, and always off under
 * `NODE_ENV=test`, so local runs, unit tests and CI send nothing.
 *
 * Every event, transaction and breadcrumb passes through `scrubEvent` before
 * it is sent (architecture.md §9): user IDs, vehicle IDs, UUIDs and
 * coordinates are removed, URLs lose their query strings, and the user,
 * cookies and request bodies are dropped.
 *
 * This module must not import `@sentry/*`, so it stays cheap to unit-test.
 */

export const FILTERED = "[Filtered]";

const SENSITIVE_KEYS = new Set([
  // identities
  "sub",
  "email",
  "user",
  "ip_address",
  // coordinates and geometry
  "lat",
  "lon",
  "lng",
  "latitude",
  "longitude",
  "coords",
  "coordinates",
  "polyline",
  "geometry",
  "bbox",
  // credentials
  "authorization",
  "cookie",
  "cookies",
  "set-cookie",
  "token",
  "access_token",
  "refresh_token",
  "password",
  "secret",
]);
// user_id, vehicleId, smartcar_vehicle_id, connection-id, ...
const ID_KEY = /(^|[_-])(user|vehicle|connection|owner)[_-]?id$/i;
const CAMEL_ID_KEY = /(user|vehicle|connection|owner)Id$/;
// origin_lat, start_lon, destLatitude, ...
const COORD_KEY = /(^|[_-])(lat|lon|lng|latitude|longitude)($|[_-])/i;
const CAMEL_COORD_KEY = /[a-z](Lat|Lon|Lng|Latitude|Longitude)$/;
// Keys holding a URL (request, fetch and navigation breadcrumbs).
const URL_KEYS = new Set(["url", "to", "from"]);

const UUID = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;
const COORD_PAIR = /-?\d{1,3}\.\d{2,}\s*[,; ]\s*-?\d{1,3}\.\d{2,}/g;
const COORD_PARAM = /\b(lat|lon|lng|latitude|longitude|near|origin|destination)=[^&\s"']*/gi;
const COORD_ASSIGN = /(^|[^A-Za-z])((?:lat|lon|lng)\w*['"]?\s*[:=]\s*)-?\d+(?:\.\d+)?/gi;
// "originLat": 37.7 (camelCase keys inside serialized JSON)
const CAMEL_COORD_ASSIGN = /([a-z])((?:Lat|Lon|Lng)\w*['"]?\s*[:=]\s*)-?\d+(?:\.\d+)?/g;
// "geometry_polyline6='_p~iF~ps|U'", "routePolyline": "..." inside reprs, JSON and URLs
const ROUTE_ASSIGN = /(\w*(?:polyline|geometry)\w*['"]?\s*[:=]\s*)(?:'[^']*'|"[^"]*"|[^\s,&]+)/gi;
const COORD_NUMBER = /^-?\d{1,3}\.\d{2,}$/;

function isSensitiveKey(key: string): boolean {
  return (
    SENSITIVE_KEYS.has(key.toLowerCase()) ||
    /polyline|geometry/i.test(key) ||
    ID_KEY.test(key) ||
    CAMEL_ID_KEY.test(key) ||
    COORD_KEY.test(key) ||
    CAMEL_COORD_KEY.test(key)
  );
}

function looksLikeCoordinate(item: unknown): boolean {
  if (typeof item === "number") {
    return Math.abs(item) <= 180 && !Number.isInteger(item);
  }
  return typeof item === "string" && COORD_NUMBER.test(item.trim());
}

/** Replace UUIDs and coordinates embedded in free text. */
export function scrubString(value: string): string {
  return value
    .replace(UUID, "[uuid]")
    .replace(COORD_PARAM, (_m, key: string) => `${key}=${FILTERED}`)
    .replace(COORD_ASSIGN, (_m, lead: string, prefix: string) => `${lead}${prefix}${FILTERED}`)
    .replace(CAMEL_COORD_ASSIGN, (_m, lead: string, prefix: string) => `${lead}${prefix}${FILTERED}`)
    .replace(ROUTE_ASSIGN, (_m, prefix: string) => `${prefix}${FILTERED}`)
    .replace(COORD_PAIR, "[coords]");
}

function stripQuery(url: string): string {
  return url.split(/[?#]/, 1)[0];
}

function scrubValue(value: unknown): unknown {
  if (typeof value === "string") return scrubString(value);
  if (Array.isArray(value)) {
    if (value.length === 2 && value.every(looksLikeCoordinate)) return "[coords]";
    return value.map(scrubValue);
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      if (isSensitiveKey(key)) {
        out[key] = FILTERED;
      } else if (URL_KEYS.has(key.toLowerCase()) && typeof item === "string") {
        out[key] = scrubString(stripQuery(item));
      } else {
        out[key] = scrubValue(item);
      }
    }
    return out;
  }
  return value;
}

/** Return a copy of a Sentry event (or breadcrumb) with personal data removed. */
export function scrubEvent<T extends object>(event: T): T {
  const copy: Record<string, unknown> = { ...(event as Record<string, unknown>) };
  delete copy.user;
  const request = copy.request;
  if (request !== null && typeof request === "object") {
    const rest: Record<string, unknown> = { ...(request as Record<string, unknown>) };
    delete rest.cookies;
    delete rest.data;
    delete rest.query_string;
    delete rest.env;
    copy.request = rest;
  }
  return scrubValue(copy) as T;
}

/** The DSN to use, or `undefined` when Sentry must stay off. */
export function sentryDsn(env: Record<string, string | undefined> = process.env): string | undefined {
  if (env.NODE_ENV === "test") return undefined;
  const dsn = env.NEXT_PUBLIC_SENTRY_DSN?.trim();
  return dsn ? dsn : undefined;
}

/** Options shared by every runtime's `Sentry.init`. */
export function sharedSentryOptions() {
  return {
    dsn: sentryDsn({
      // Spelled out so Next.js inlines the public value into each bundle.
      NODE_ENV: process.env.NODE_ENV,
      NEXT_PUBLIC_SENTRY_DSN: process.env.NEXT_PUBLIC_SENTRY_DSN,
    }),
    environment:
      process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ||
      process.env.NEXT_PUBLIC_VERCEL_ENV ||
      process.env.NODE_ENV,
    sendDefaultPii: false,
    // Server runtime: never attach stack-frame locals (they can hold secrets).
    // Already the Node SDK default; pinned so it can't be switched on by accident.
    includeLocalVariables: false,
    // Errors only by default; tracing would also send page URLs as spans.
    tracesSampleRate: 0,
    beforeSend: <E extends object>(event: E) => scrubEvent(event),
    beforeSendTransaction: <E extends object>(event: E) => scrubEvent(event),
    beforeBreadcrumb: <B extends object>(crumb: B) => scrubEvent(crumb),
  };
}
