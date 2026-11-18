"use server";

import type { components } from "@chargewise/api-client";

import { createApi } from "@/lib/api";
import { stationsErrorCode, type StationsErrorCode } from "@/lib/stations";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";
import type { AlongRouteFilters } from "@/lib/trip-url";

export type DirectionsRoute = components["schemas"]["DirectionsRouteOut"];
export type RouteStation = components["schemas"]["RouteStationOut"];
export type NoRouteReason = NonNullable<components["schemas"]["DirectionsResponse"]["no_route_reason"]>;

export type DirectionsErrorCode =
  | "MAPBOX_UNREACHABLE"
  | "MAPBOX_NOT_CONFIGURED"
  | "MAPBOX_ERROR"
  | "VALIDATION_ERROR"
  | "UNAUTHENTICATED"
  | "UNKNOWN";

export type DirectionsResult =
  | { routes: DirectionsRoute[]; noRouteReason: NoRouteReason | null }
  | { error: DirectionsErrorCode };

export type AlongRouteResult =
  | {
      items: RouteStation[];
      totalResults: number;
      unknownPowerCount: number;
      truncated: boolean;
    }
  | { error: StationsErrorCode | "UNAUTHENTICATED" };

type Point = { lat: number; lon: number };

/** Matches the contract's limit on `geometry_polyline6` (api §3.17). */
const MAX_POLYLINE_LENGTH = 200_000;

async function sessionApi() {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session ? createApi({ accessToken: session.access_token }) : null;
}

function validPoint(p: Point | null | undefined): p is Point {
  return (
    !!p &&
    Number.isFinite(p.lat) &&
    Number.isFinite(p.lon) &&
    Math.abs(p.lat) <= 90 &&
    Math.abs(p.lon) <= 180
  );
}

function directionsErrorCode(body: unknown, status: number): DirectionsErrorCode {
  if (status === 401) return "UNAUTHENTICATED";
  const code =
    body && typeof body === "object" && "error" in body
      ? (body as { error?: { code?: unknown } }).error?.code
      : undefined;
  if (
    code === "MAPBOX_UNREACHABLE" ||
    code === "MAPBOX_NOT_CONFIGURED" ||
    code === "MAPBOX_ERROR" ||
    code === "VALIDATION_ERROR"
  ) {
    return code;
  }
  return "UNKNOWN";
}

/**
 * Driving routes between two points (`POST /api/v1/routes/directions`,
 * api §3.15). Runs on the server so the Supabase token stays out of the
 * browser's own requests. Coordinates are never logged (D13).
 */
export async function getDirections(input: {
  origin: Point;
  destination: Point;
}): Promise<DirectionsResult> {
  if (!validPoint(input?.origin) || !validPoint(input?.destination)) {
    return { error: "VALIDATION_ERROR" };
  }
  const api = await sessionApi();
  if (!api) return { error: "UNAUTHENTICATED" };
  try {
    const { data, error, response } = await api.POST("/api/v1/routes/directions", {
      body: {
        origin: { lat: input.origin.lat, lon: input.origin.lon },
        destination: { lat: input.destination.lat, lon: input.destination.lon },
      },
    });
    if (!data) return { error: directionsErrorCode(error, response.status) };
    return { routes: data.routes, noRouteReason: data.no_route_reason };
  } catch {
    return { error: "MAPBOX_UNREACHABLE" };
  }
}

/**
 * Stations in a corridor around one route (`POST /api/v1/stations/along-route`,
 * api §3.17), in order along the route.
 */
export async function getStationsAlongRoute(input: {
  geometry: string;
  filters: AlongRouteFilters;
}): Promise<AlongRouteResult> {
  if (
    typeof input?.geometry !== "string" ||
    input.geometry.length === 0 ||
    input.geometry.length > MAX_POLYLINE_LENGTH ||
    !input.filters
  ) {
    return { error: "VALIDATION_ERROR" };
  }
  const api = await sessionApi();
  if (!api) return { error: "UNAUTHENTICATED" };
  const f = input.filters;
  try {
    const { data, error, response } = await api.POST("/api/v1/stations/along-route", {
      body: {
        geometry_polyline6: input.geometry,
        corridor_km: f.corridor_km,
        charging_level: f.charging_level,
        limit: f.limit,
        ...(f.connector ? { connector: f.connector } : {}),
        ...(f.network ? { network: f.network } : {}),
        ...(f.min_power_kw !== undefined ? { min_power_kw: f.min_power_kw } : {}),
        ...(f.vehicle_id ? { vehicle_id: f.vehicle_id } : {}),
      },
    });
    if (!data) {
      if (response.status === 401) return { error: "UNAUTHENTICATED" };
      return { error: stationsErrorCode(error) };
    }
    return {
      items: data.items,
      totalResults: data.total_results,
      unknownPowerCount: data.unknown_power_count,
      truncated: data.truncated,
    };
  } catch {
    return { error: "STATIONS_UNREACHABLE" };
  }
}
