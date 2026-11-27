import { env } from "@/lib/env";

/**
 * Whether `email` is the shared public demo account (P6-F2, ADR 0012). The
 * demo is an account on the main deployment, not a deployment of its own, so
 * the web app recognises it by the public demo email. This only changes what
 * the page shows (banner, simulated analytics by default); the API enforces
 * the demo account's read-only rules by its user ID.
 */
export function isDemoEmail(email: string | null | undefined): boolean {
  const demo = env.demoCredentials;
  if (!email || !demo) return false;
  return email.trim().toLowerCase() === demo.email.trim().toLowerCase();
}
