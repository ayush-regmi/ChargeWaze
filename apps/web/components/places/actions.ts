"use server";

import type { components } from "@chargewise/api-client";

import { createApi } from "@/lib/api";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

export type PlaceSuggestion = components["schemas"]["PlaceSuggestionOut"];
export type Place = components["schemas"]["PlaceOut"];

export type PlaceErrorCode = "unauthenticated" | "invalid" | "not_found" | "unavailable";
export type SuggestResult = { items: PlaceSuggestion[] } | { error: PlaceErrorCode };
export type RetrieveResult = { place: Place } | { error: PlaceErrorCode };

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function sessionApi() {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session ? createApi({ accessToken: session.access_token }) : null;
}

function errorFor(status: number): PlaceErrorCode {
  if (status === 401) return "unauthenticated";
  if (status === 404) return "not_found";
  if (status === 422) return "invalid";
  return "unavailable";
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
    const { data, response } = await api.GET("/api/v1/places/suggest", {
      params: {
        query: {
          q,
          session_token: input.sessionToken,
          ...(near ? { lat: near.lat, lon: near.lon } : {}),
        },
      },
    });
    if (!data) return { error: errorFor(response.status) };
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
    const { data, response } = await api.GET("/api/v1/places/{place_id}", {
      params: { path: { place_id: input.id }, query: { session_token: input.sessionToken } },
    });
    if (!data) return { error: errorFor(response.status) };
    return { place: data };
  } catch {
    return { error: "unavailable" };
  }
}
