"use server";

import { requireSessionApi } from "@/lib/session-api";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type FavoriteResult = { error: string } | undefined;

/**
 * Star or unstar a station (`PUT` / `DELETE /api/v1/stations/{id}/favorite`,
 * api §3.19a–b). Both are idempotent, so a retried click is harmless.
 *
 * No `revalidatePath`: that would re-render `/charging` and re-run its
 * provider search on every click. The Favorites tab refreshes itself.
 */
export async function setStationFavorite(
  stationId: string,
  favorite: boolean,
): Promise<FavoriteResult> {
  if (!UUID_RE.test(stationId)) {
    return { error: "That station couldn't be found." };
  }
  const api = await requireSessionApi(`/charging/${stationId}`);
  const params = { params: { path: { station_id: stationId } } };
  let status = 0;
  let code: string | undefined;
  try {
    const { response, error } = favorite
      ? await api.PUT("/api/v1/stations/{station_id}/favorite", params)
      : await api.DELETE("/api/v1/stations/{station_id}/favorite", params);
    status = response.status;
    code = (error as { error?: { code?: string } } | undefined)?.error?.code;
  } catch {
    status = 0;
  }
  if (status === 204) return undefined;
  if (status === 404) return { error: "That station couldn't be found." };
  if (code === "FAVORITES_LIMIT") {
    return { error: "You've reached the limit of 500 favorite stations. Remove one first." };
  }
  return {
    error: favorite
      ? "Couldn't add this station to favorites. Please try again."
      : "Couldn't remove this station from favorites. Please try again.",
  };
}
