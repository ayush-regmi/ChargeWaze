import { redirect } from "next/navigation";

import { createApi, type ApiClient } from "@/lib/api";
import { isDemoEmail } from "@/lib/demo";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

/**
 * API client bound to the signed-in user's Supabase token. Redirects to sign-in
 * (returning to `next`) when there is no session. Server-only.
 */
export async function requireSessionApi(next: string): Promise<ApiClient> {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) {
    redirect(`/sign-in?next=${encodeURIComponent(next)}`);
  }
  return createApi({ accessToken: session.access_token });
}

/**
 * Whether the signed-in user is the public demo account (`lib/demo.ts`).
 * False with no session. Server-only; for display, the API enforces the rules.
 */
export async function sessionIsDemo(): Promise<boolean> {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return isDemoEmail(session?.user.email);
}
