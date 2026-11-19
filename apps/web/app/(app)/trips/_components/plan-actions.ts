"use server";

import { createApi } from "@/lib/api";
import { planErrorOutcome, type PlanOutcome, type PlanPreferences } from "@/lib/plan-format";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

type Endpoint = { lat: number; lon: number; label?: string | null };

export type PlanTripInput = {
  vehicleId: string;
  origin: Endpoint;
  destination: Endpoint;
  routeIndex: number;
  preferences?: PlanPreferences | null;
  startingSocOverride?: number | null;
};

export type SaveTripResult = { ok: true } | { ok: false; error: "NOT_FOUND" | "UNAUTHENTICATED" | "UNKNOWN" };

const MAX_LABEL_LENGTH = 200;

async function sessionApi() {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session ? createApi({ accessToken: session.access_token }) : null;
}

function validEndpoint(p: Endpoint | null | undefined): p is Endpoint {
  return (
    !!p &&
    Number.isFinite(p.lat) &&
    Number.isFinite(p.lon) &&
    Math.abs(p.lat) <= 90 &&
    Math.abs(p.lon) <= 180
  );
}

function endpointBody(p: Endpoint) {
  const label = typeof p.label === "string" ? p.label.trim().slice(0, MAX_LABEL_LENGTH) : "";
  return { lat: p.lat, lon: p.lon, ...(label ? { label } : {}) };
}

/**
 * Plan charging stops on one route (`POST /api/v1/routes/plan`, api §3.7).
 * The server re-fetches the route itself; only endpoints and the route index
 * are sent. Every result, feasible or not, is stored by the API as an
 * unsaved trip. Coordinates are never logged.
 */
export async function planTrip(input: PlanTripInput): Promise<PlanOutcome> {
  if (
    typeof input?.vehicleId !== "string" ||
    !validEndpoint(input.origin) ||
    !validEndpoint(input.destination) ||
    !Number.isInteger(input.routeIndex) ||
    input.routeIndex < 0
  ) {
    return { kind: "error", code: "VALIDATION_ERROR" };
  }
  const override = input.startingSocOverride;
  if (override !== undefined && override !== null && !(override >= 0 && override <= 1)) {
    return { kind: "error", code: "VALIDATION_ERROR" };
  }
  const api = await sessionApi();
  if (!api) return { kind: "error", code: "UNAUTHENTICATED" };
  try {
    const { data, error, response } = await api.POST("/api/v1/routes/plan", {
      body: {
        vehicle_id: input.vehicleId,
        origin: endpointBody(input.origin),
        destination: endpointBody(input.destination),
        route_index: input.routeIndex,
        ...(input.preferences ? { preferences: input.preferences } : {}),
        ...(override !== undefined && override !== null ? { starting_soc_override: override } : {}),
      },
    });
    if (data) return { kind: "plan", plan: data };
    return planErrorOutcome(error, response.status);
  } catch {
    return { kind: "error", code: "UNKNOWN" };
  }
}

/** Keep a planned trip (`POST /api/v1/trips/{id}/save`, api §3.9b). Idempotent. */
export async function saveTrip(tripId: string): Promise<SaveTripResult> {
  if (typeof tripId !== "string" || !/^[0-9a-f-]{36}$/i.test(tripId)) {
    return { ok: false, error: "NOT_FOUND" };
  }
  const api = await sessionApi();
  if (!api) return { ok: false, error: "UNAUTHENTICATED" };
  try {
    const { data, response } = await api.POST("/api/v1/trips/{trip_id}/save", {
      params: { path: { trip_id: tripId } },
    });
    if (data) return { ok: true };
    if (response.status === 401) return { ok: false, error: "UNAUTHENTICATED" };
    if (response.status === 404) return { ok: false, error: "NOT_FOUND" };
    return { ok: false, error: "UNKNOWN" };
  } catch {
    return { ok: false, error: "UNKNOWN" };
  }
}
