"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { requireSessionApi } from "@/lib/session-api";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

import { DELETE_CONFIRMATION } from "./privacy";

export type DeleteAccountResult = { error: string } | undefined;

/**
 * Delete the account (`DELETE /api/v1/me`, api §3.1b), then end the session
 * and go to sign-in. The API removes every row, disconnects the cars from
 * Smartcar and deletes the sign-in account; steps that fail on Smartcar's or
 * Supabase's side are retried by the server, so a 204 is final.
 */
export async function deleteAccount(confirmation: string): Promise<DeleteAccountResult> {
  if (confirmation !== DELETE_CONFIRMATION) {
    return { error: `Type ${DELETE_CONFIRMATION} to confirm.` };
  }
  const api = await requireSessionApi("/profile");
  let status = 0;
  try {
    const { response } = await api.DELETE("/api/v1/me");
    status = response.status;
  } catch {
    status = 0;
  }
  if (status !== 204) {
    return { error: "Couldn't delete your account. Please try again." };
  }
  try {
    const supabase = await createSupabaseClient();
    // The auth user is already gone; this clears the session cookies.
    await supabase.auth.signOut();
  } catch {
    // Cookies are cleared locally even if Supabase answers with an error.
  }
  redirect("/sign-in");
}

export type ConsentResult = { granted: boolean } | { error: string };

/**
 * Record the user's answer to the privacy notice (`POST /api/v1/me/consents`,
 * api §3.1d). Each answer is a new audit row.
 */
export async function recordPrivacyConsent(
  version: string,
  granted: boolean,
): Promise<ConsentResult> {
  const api = await requireSessionApi("/profile");
  let status = 0;
  let saved: boolean | null | undefined;
  try {
    const { data, response } = await api.POST("/api/v1/me/consents", {
      body: { kind: "privacy_notice", version, granted },
    });
    status = response.status;
    saved = data?.granted;
  } catch {
    status = 0;
  }
  if (status !== 201 || typeof saved !== "boolean") {
    return {
      error:
        status === 422
          ? "The privacy notice has changed. Reload the page to see the new version."
          : "Couldn't save your answer. Please try again.",
    };
  }
  revalidatePath("/profile");
  return { granted: saved };
}
