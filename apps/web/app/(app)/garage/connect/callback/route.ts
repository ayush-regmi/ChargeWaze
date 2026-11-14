import { NextResponse, type NextRequest } from "next/server";

import { createApi } from "@/lib/api";
import { GARAGE_PATH, connectResultUrl } from "@/lib/connect";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

/**
 * Smartcar Connect redirect target (``SMARTCAR_REDIRECT_URI``).
 *
 * Connect finishes here, on the web origin, because this is where the user's
 * Supabase session lives. The query is forwarded to the backend with the
 * *current* user's token, and the backend refuses a ``state`` that belongs
 * to anyone else. That stops an attacker from sending their own Connect link
 * to a victim and receiving the victim's car.
 */
export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const params = url.searchParams;

  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) {
    const signIn = new URL("/sign-in", url.origin);
    signIn.searchParams.set("next", GARAGE_PATH);
    return NextResponse.redirect(signIn);
  }

  const state = params.get("state");
  if (!state) {
    return NextResponse.redirect(connectResultUrl(GARAGE_PATH, "error", url.origin));
  }

  const api = createApi({ accessToken: session.access_token });
  const { data } = await api.POST("/api/v1/vehicles/connect/complete", {
    body: {
      state,
      user_id: params.get("user_id"),
      external_id: params.get("external_id"),
      error: params.get("error"),
    },
  });

  if (!data) {
    return NextResponse.redirect(connectResultUrl(GARAGE_PATH, "error", url.origin));
  }
  return NextResponse.redirect(
    connectResultUrl(data.redirect_after, data.result, url.origin),
  );
}
