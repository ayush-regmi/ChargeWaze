import { NextResponse, type NextRequest } from "next/server";

import { signInErrorUrl } from "@/lib/auth-error";
import { safeNextPath } from "@/lib/safe-next";
import { createClient } from "@/lib/supabase/server";

/**
 * Handles the OAuth / email-confirmation redirect from Supabase. Exchanges
 * the ``code`` for a session cookie and then forwards the user to ``next``.
 * If Supabase reports an error, or the exchange fails, the user goes back to
 * ``/sign-in`` with ``error`` / ``error_code`` so the page can explain.
 */
export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const next = safeNextPath(url.searchParams.get("next"));

  const providerError = url.searchParams.get("error");
  const providerCode = url.searchParams.get("error_code");
  if (!code && (providerError || providerCode)) {
    return NextResponse.redirect(
      signInErrorUrl(url.origin, { error: providerError, code: providerCode }, next),
    );
  }

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) {
      return NextResponse.redirect(
        signInErrorUrl(url.origin, { error: "exchange_failed", code: error.code }, next),
      );
    }
  }

  return NextResponse.redirect(new URL(next, url.origin));
}
