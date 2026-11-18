"use server";

import { revalidatePath } from "next/cache";

import type { Preferences, PreferencesBody } from "@/components/profile/preferences";
import { requireSessionApi } from "@/lib/session-api";

export type UpdatePreferencesResult = { preferences: Preferences } | { error: string };

/**
 * Save the Profile preferences (`PATCH /api/v1/me/preferences`, api §3.1a).
 * The form has already validated `body` with the same rules, so a 422 here
 * means the two drifted and the message stays generic. On success the
 * Profile and Dashboard are revalidated so they render with the new units.
 */
export async function updatePreferences(
  body: PreferencesBody,
): Promise<UpdatePreferencesResult> {
  const api = await requireSessionApi("/profile");
  let status = 0;
  let preferences: Preferences | undefined;
  try {
    const { data, response } = await api.PATCH("/api/v1/me/preferences", {
      body: {
        units: body.units,
        reserve_soc: body.reserve_soc,
        preferred_charge_to: body.preferred_charge_to,
        max_detour_minutes: body.max_detour_minutes,
        preferred_networks: body.preferred_networks,
      },
    });
    status = response.status;
    preferences = data;
  } catch {
    status = 0;
  }
  if (status !== 200 || !preferences) {
    return {
      error:
        status === 422
          ? "Some values weren't accepted. Check them and try again."
          : "Couldn't save your preferences. Please try again.",
    };
  }
  revalidatePath("/profile");
  revalidatePath("/dashboard");
  return { preferences };
}
