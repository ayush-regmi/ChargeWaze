"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { requireSessionApi } from "@/lib/session-api";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type DeleteTripResult = { error: string } | undefined;

/**
 * Delete a trip (`DELETE /api/v1/trips/{id}`, api-contracts §3.9a) and go back
 * to Saved trips. A 404 means it's already gone (or never was the caller's),
 * so that also lands on the list.
 */
export async function deleteTrip(tripId: string): Promise<DeleteTripResult> {
  if (!UUID_RE.test(tripId)) {
    return { error: "That trip couldn't be found." };
  }
  const api = await requireSessionApi(`/trips/${tripId}`);
  let status = 0;
  try {
    const { response } = await api.DELETE("/api/v1/trips/{trip_id}", {
      params: { path: { trip_id: tripId } },
    });
    status = response.status;
  } catch {
    status = 0;
  }
  if (status !== 204 && status !== 404) {
    return { error: "Couldn't delete the trip. Please try again." };
  }
  revalidatePath("/trips/saved");
  redirect("/trips/saved");
}
