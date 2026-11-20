/**
 * Thin accessor for ``NEXT_PUBLIC_*`` env vars with helpful fallbacks and
 * runtime assertions. Throws early if a required value is missing so misconfig
 * surfaces before a confusing 401.
 */

function required(name: string, value: string | undefined): string {
  if (!value || value.length === 0) {
    throw new Error(
      `Missing required env var ${name}. See apps/web/.env.example for setup.`,
    );
  }
  return value;
}

export const env = {
  get apiBaseUrl() {
    return (
      process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ??
      "http://localhost:8000"
    );
  },
  get supabaseUrl() {
    return required("NEXT_PUBLIC_SUPABASE_URL", process.env.NEXT_PUBLIC_SUPABASE_URL);
  },
  get supabaseAnonKey() {
    return required(
      "NEXT_PUBLIC_SUPABASE_ANON_KEY",
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    );
  },
  get googleAuthEnabled() {
    return (
      (process.env.NEXT_PUBLIC_AUTH_GOOGLE_ENABLED ?? "true").toLowerCase() ===
      "true"
    );
  },
  /** Public demo deployment (P6-F2): banner, demo sign-in, simulated analytics. */
  get demoMode() {
    return (process.env.NEXT_PUBLIC_DEMO_MODE ?? "false").toLowerCase() === "true";
  },
  /** The shared demo account; public on purpose (only simulated data). */
  get demoCredentials(): { email: string; password: string } | null {
    const email = process.env.NEXT_PUBLIC_DEMO_EMAIL ?? "";
    const password = process.env.NEXT_PUBLIC_DEMO_PASSWORD ?? "";
    return email && password ? { email, password } : null;
  },
  get appleAuthEnabled() {
    return (
      (process.env.NEXT_PUBLIC_AUTH_APPLE_ENABLED ?? "false").toLowerCase() ===
      "true"
    );
  },
};
