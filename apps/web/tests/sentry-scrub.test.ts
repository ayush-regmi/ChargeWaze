import { describe, expect, it } from "vitest";

import {
  FILTERED,
  scrubEvent,
  scrubString,
  sentryDsn,
  sharedSentryOptions,
} from "@/sentry.shared.config";

const USER_ID = "3f2b9c1e-7a4d-4e8b-9c3a-2d1f0e6b5a49";
const VEHICLE_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const LAT = 37.774929;
const LON = -122.419416;
const DSN = "https://publickey@sentry.example.invalid/1";

describe("sentryDsn", () => {
  it("is off without a DSN", () => {
    expect(sentryDsn({ NODE_ENV: "production" })).toBeUndefined();
    expect(sentryDsn({ NODE_ENV: "production", NEXT_PUBLIC_SENTRY_DSN: "  " })).toBeUndefined();
  });

  it("is off under NODE_ENV=test even with a DSN", () => {
    expect(sentryDsn({ NODE_ENV: "test", NEXT_PUBLIC_SENTRY_DSN: DSN })).toBeUndefined();
  });

  it("returns the DSN outside tests", () => {
    expect(sentryDsn({ NODE_ENV: "production", NEXT_PUBLIC_SENTRY_DSN: DSN })).toBe(DSN);
  });

  it("leaves the shared options without a DSN in this test run", () => {
    const options = sharedSentryOptions();
    expect(options.dsn).toBeUndefined();
    expect(options.sendDefaultPii).toBe(false);
    expect(options.includeLocalVariables).toBe(false);
  });
});

describe("scrubString", () => {
  it("removes UUIDs and coordinates from free text", () => {
    const out = scrubString(
      `user ${USER_ID} vehicle ${VEHICLE_ID} at (${LAT}, ${LON}) ` +
        `/stations?lat=${LAT}&lon=${LON}&radius=5 {"originLat":${LAT},"dest_lng":${LON}}`,
    );
    for (const secret of [USER_ID, VEHICLE_ID, String(LAT), String(LON)]) {
      expect(out).not.toContain(secret);
    }
    expect(out).toContain("radius=5");
  });
});

describe("route geometry", () => {
  const POLYLINE6 = "_p~iF~ps|U_ulLnnqC";

  it("filters any key containing polyline or geometry", () => {
    const out = scrubEvent({
      extra: { geometry_polyline6: POLYLINE6, routeGeometry: { type: "LineString" }, distance_m: 1000 },
    });
    expect(out.extra).toEqual({
      geometry_polyline6: FILTERED,
      routeGeometry: FILTERED,
      distance_m: 1000,
    });
  });

  it("scrubs geometry assignments inside serialized text and URLs", () => {
    const out = scrubString(
      `{"geometry_polyline6":"${POLYLINE6}","distance_m":1000} ` +
        `geometry_polyline6='${POLYLINE6}' /plan?geometry_polyline6=${POLYLINE6}`,
    );
    expect(out).not.toContain(POLYLINE6);
    expect(out).toContain('"distance_m":1000');
  });
});

describe("scrubEvent", () => {
  const event = {
    message: `plan failed for ${USER_ID}`,
    user: { id: USER_ID, email: "someone@example.com", ip_address: "1.2.3.4" },
    request: {
      url: `https://app.example.com/trips/new?origin=${LAT},${LON}&vehicle=${VEHICLE_ID}`,
      query_string: `lat=${LAT}&lon=${LON}`,
      data: { origin: { lat: LAT, lon: LON } },
      cookies: { "sb-access-token": "secret" },
      headers: { Authorization: "Bearer abc.def.ghi", "User-Agent": "vitest" },
    },
    tags: { userId: USER_ID, page: "trips" },
    extra: {
      vehicle_id: VEHICLE_ID,
      originLat: LAT,
      dest_lon: LON,
      coordinates: [[LON, LAT]],
      waypoints: [[LAT, LON], [String(LAT), String(LON)]],
      polyline: "_p~iF~ps|U_ulLnnqC",
      stopCount: 3,
    },
    breadcrumbs: [
      { category: "navigation", data: { from: `/trips?lat=${LAT}`, to: `/stations/${USER_ID}` } },
      { category: "fetch", data: { url: `https://api.example.com/api/v1/stations?lat=${LAT}` } },
      { category: "console", message: `vehicle at ${LAT},${LON}` },
    ],
  };

  it("removes user IDs, coordinates, credentials and query strings", () => {
    const out = scrubEvent(event);
    const dumped = JSON.stringify(out);

    for (const secret of [
      USER_ID,
      VEHICLE_ID,
      String(LAT),
      String(LON),
      "someone@example.com",
      "1.2.3.4",
      "abc.def.ghi",
      "_p~iF~ps|U_ulLnnqC",
      "sb-access-token",
    ]) {
      expect(dumped).not.toContain(secret);
    }
    expect(out).not.toHaveProperty("user");
    expect(out.request).not.toHaveProperty("query_string");
    expect(out.request).not.toHaveProperty("data");
    expect(out.request).not.toHaveProperty("cookies");
    expect(out.request.url).toBe("https://app.example.com/trips/new");
    expect(out.request.headers.Authorization).toBe(FILTERED);
    expect(out.tags).toEqual({ userId: FILTERED, page: "trips" });
  });

  it("keeps non-sensitive values and does not mutate the input", () => {
    const out = scrubEvent(event);
    expect(out.extra.stopCount).toBe(3);
    expect(out.request.headers["User-Agent"]).toBe("vitest");
    expect(event.user.id).toBe(USER_ID);
  });

  it("is used by every hook in the shared options", () => {
    const options = sharedSentryOptions();
    for (const hook of [options.beforeSend, options.beforeSendTransaction, options.beforeBreadcrumb]) {
      expect(JSON.stringify(hook(event))).not.toContain(USER_ID);
    }
  });
});
