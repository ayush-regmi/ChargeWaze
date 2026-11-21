/**
 * Validate the post-auth ``next`` redirect target.
 *
 * ``next`` arrives from the query string, so anyone can craft a sign-in link
 * with ``next=//evil.example``. Browsers resolve ``//host``, ``/\host`` and
 * tab/newline-smuggled variants as a different origin, which turns our sign-in
 * flow into an open redirect. Only same-origin relative paths are allowed;
 * everything else falls back to the dashboard.
 */

export const DEFAULT_NEXT_PATH = "/dashboard";

// Placeholder origin used only to resolve the path. ``.invalid`` is reserved
// (RFC 2606), so it can never collide with a real host.
const PROBE_ORIGIN = "http://chargewaze.invalid";

export function safeNextPath(
  next: string | null | undefined,
  fallback: string = DEFAULT_NEXT_PATH,
): string {
  if (!next || !next.startsWith("/")) {
    return fallback;
  }

  let resolved: URL;
  try {
    resolved = new URL(next, PROBE_ORIGIN);
  } catch {
    return fallback;
  }

  if (resolved.origin !== PROBE_ORIGIN) {
    return fallback;
  }

  const path = `${resolved.pathname}${resolved.search}${resolved.hash}`;

  // Dot segments are removed during parsing, so an input like ``/.//evil``
  // passes the origin check above but normalizes to ``//evil``, which the
  // caller would read as a protocol-relative URL. Validate the *output*: it
  // must not start with ``//`` and must resolve back to itself on our origin.
  if (path.startsWith("//") || !isSameOriginPath(path)) {
    return fallback;
  }

  return path;
}

function isSameOriginPath(path: string): boolean {
  try {
    const reparsed = new URL(path, PROBE_ORIGIN);
    return (
      reparsed.origin === PROBE_ORIGIN &&
      `${reparsed.pathname}${reparsed.search}${reparsed.hash}` === path
    );
  } catch {
    return false;
  }
}
