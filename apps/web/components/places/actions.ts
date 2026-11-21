"use server";

import type { components } from "@chargewaze/api-client";

import { createApi } from "@/lib/api";
import { retryAfterSeconds } from "@/lib/retry-after";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

export type PlaceSuggestion = components["schemas"]["PlaceSuggestionOut"];
export type Place = components["schemas"]["PlaceOut"];

export type PlaceErrorCode =
  | "unauthenticated"
  | "invalid"
  | "not_found"
  | "rate_limited"
  | "unavailable";
/** A failure; `retryAfterSeconds` comes with `rate_limited` (api §2 `RATE_LIMITED`). */
export type PlaceError = { error: PlaceErrorCode; retryAfterSeconds?: number | null };
export type SuggestResult = { items: PlaceSuggestion[] } | PlaceError;
export type RetrieveResult = { place: Place } | PlaceError;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function sessionApi() {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session ? createApi({ accessToken: session.access_token }) : null;
}

function errorFor(status: number, body: unknown): PlaceError {
  if (status === 401) return { error: "unauthenticated" };
  if (status === 404) return { error: "not_found" };
  if (status === 422) return { error: "invalid" };
  const error = (body as { error?: { code?: unknown; details?: unknown } } | undefined)?.error;
  if (error?.code === "RATE_LIMITED") {
    return { error: "rate_limited", retryAfterSeconds: retryAfterSeconds(error.details) };
  }
  return { error: "unavailable" };
}

function validNear(near: { lat: number; lon: number } | null | undefined) {
  return (
    near &&
    Number.isFinite(near.lat) &&
    Number.isFinite(near.lon) &&
    Math.abs(near.lat) <= 90 &&
    Math.abs(near.lon) <= 180
  );
}

/**
 * Place autocomplete through the backend (`GET /api/v1/places/suggest`,
 * api §3.13; D2). Runs on the server so the Supabase token never reaches the
 * browser's own fetches. Search text is never logged.
 */
export async function suggestPlaces(input: {
  q: string;
  sessionToken: string;
  near?: { lat: number; lon: number } | null;
}): Promise<SuggestResult> {
  const q = input.q.trim();
  if (q.length < 3 || q.length > 200 || !UUID_RE.test(input.sessionToken)) {
    return { error: "invalid" };
  }
  const api = await sessionApi();
  if (!api) return { error: "unauthenticated" };
  try {
    const near = validNear(input.near) ? input.near! : null;
    const { data, error, response } = await api.GET("/api/v1/places/suggest", {
      params: {
        query: {
          q,
          session_token: input.sessionToken,
          ...(near ? { lat: near.lat, lon: near.lon } : {}),
        },
      },
    });
    if (!data) return errorFor(response.status, error);
    return { items: data.items };
  } catch {
    return { error: "unavailable" };
  }
}

/** Resolve a suggestion to coordinates (`GET /api/v1/places/{id}`, api §3.14). */
export async function retrievePlace(input: {
  id: string;
  sessionToken: string;
}): Promise<RetrieveResult> {
  if (!input.id || input.id.length > 512 || !UUID_RE.test(input.sessionToken)) {
    return { error: "invalid" };
  }
  const api = await sessionApi();
  if (!api) return { error: "unauthenticated" };
  try {
    const { data, error, response } = await api.GET("/api/v1/places/{place_id}", {
      params: { path: { place_id: input.id }, query: { session_token: input.sessionToken } },
    });
    if (!data) return errorFor(response.status, error);
    return { place: data };
  } catch {
    return { error: "unavailable" };
  }
}
