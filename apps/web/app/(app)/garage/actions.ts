"use server";

import { redirect } from "next/navigation";

import { createApi } from "@/lib/api";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

/**
 * Start Smartcar Connect: ask the backend for an authorization URL and send
 * the browser there. Smartcar returns to ``/garage/connect/callback``, which
 * completes Connect with the signed-in user's token and redirects to
 * ``/garage?connect=…``.
 */
export async function startVehicleConnect(): Promise<never> {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) {
    redirect("/sign-in?next=/garage");
  }

  const api = createApi({ accessToken: session.access_token });
  const { data } = await api.POST("/api/v1/vehicles/connect", {
    body: { redirect_after: "/garage" },
  });

  if (!data) {
    redirect("/garage?connect=error");
  }
  redirect(data.authorization_url);
}
