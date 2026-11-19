"use server";

import { revalidatePath } from "next/cache";

import { requireSessionApi } from "@/lib/session-api";

import type { ReviewStatus } from "./observation-format";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type ReviewResult = { status: ReviewStatus } | { error: string };

const PATHS = {
  confirm: "/api/v1/observed-trips/{trip_id}/confirm",
  reject: "/api/v1/observed-trips/{trip_id}/reject",
} as const;

async function review(id: string, action: keyof typeof PATHS): Promise<ReviewResult> {
  if (!UUID_RE.test(id)) return { error: "This drive couldn't be found." };
  const api = await requireSessionApi("/trips/saved?view=observed");
  let status = 0;
  let leg: { review_status: ReviewStatus; planned_trip_id: string | null } | undefined;
  try {
    const res = await api.POST(PATHS[action], { params: { path: { trip_id: id } } });
    status = res.response.status;
    leg = res.data;
  } catch {
    status = 0;
  }
  if (status === 404) return { error: "This drive no longer exists. Refresh the list." };
  if (status !== 200 || !leg) {
    return {
      error: `Couldn't ${action} this drive. Please try again.`,
    };
  }
  revalidatePath("/trips/saved");
  if (leg.planned_trip_id) revalidatePath(`/trips/${leg.planned_trip_id}`);
  return { status: leg.review_status };
}

/** Mark a drive as a real trip of the caller's (api-contracts §3.18c). */
export async function confirmObservedTrip(id: string): Promise<ReviewResult> {
  return review(id, "confirm");
}

/** Mark a drive as not the caller's trip (api-contracts §3.18d). */
export async function rejectObservedTrip(id: string): Promise<ReviewResult> {
  return review(id, "reject");
}
