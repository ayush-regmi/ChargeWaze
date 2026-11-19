// Server-side instrumentation (Next.js calls `register` once per server).
// Sentry stays off unless NEXT_PUBLIC_SENTRY_DSN is set; see sentry.shared.config.ts.
import * as Sentry from "@sentry/nextjs";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

// Reports errors thrown in server components, route handlers and server actions.
// A no-op when Sentry was not initialised.
export const onRequestError = Sentry.captureRequestError;
