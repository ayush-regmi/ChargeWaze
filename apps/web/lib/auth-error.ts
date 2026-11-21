/**
 * Turn the error Supabase Auth hands back after a failed sign-in into a short
 * message for the sign-in page.
 *
 * Supabase redirects with ``?error=…&error_code=…&error_description=…`` when
 * an OAuth or email-link sign-in fails (for example ``bad_oauth_state`` when
 * the OAuth state has expired). ``/auth/callback`` forwards the same two
 * params when it can't finish the sign-in itself. The values come from the
 * query string, so they are only ever used as lookup keys: the raw text,
 * including ``error_description``, is never shown.
 */

export const GENERIC_AUTH_ERROR = "Sign-in didn't finish. Please try again.";
export const EXPIRED_AUTH_ERROR = "Your sign-in link expired. Please try again.";

// Supabase Auth error codes that mean "the link or flow is too old; start again".
const EXPIRED_CODES: ReadonlySet<string> = new Set([
  "bad_oauth_state",
  "flow_state_expired",
  "flow_state_not_found",
  "otp_expired",
]);

type Params = Pick<URLSearchParams, "get">;

/** The message to show, or ``null`` if the URL carries no auth error. */
export function authErrorMessage(params: Params): string | null {
  const error = params.get("error");
  const code = params.get("error_code");
  if (!error && !code) return null;
  if (code && EXPIRED_CODES.has(code)) return EXPIRED_AUTH_ERROR;
  return GENERIC_AUTH_ERROR;
}

/**
 * The ``/sign-in`` URL that carries an auth error. Only ``error``,
 * ``error_code`` and the (already validated) ``next`` path are kept.
 */
export function signInErrorUrl(
  origin: string,
  { error, code }: { error?: string | null; code?: string | null },
  next?: string,
): URL {
  const url = new URL("/sign-in", origin);
  url.searchParams.set("error", error || "server_error");
  if (code) url.searchParams.set("error_code", code);
  if (next) url.searchParams.set("next", next);
  return url;
}
