// Browser instrumentation (runs before the app becomes interactive).
// Sentry stays off unless NEXT_PUBLIC_SENTRY_DSN is set; see sentry.shared.config.ts.
// No Session Replay: it would record the map and addresses on screen.
import * as Sentry from "@sentry/nextjs";

import { sharedSentryOptions } from "./sentry.shared.config";

const options = sharedSentryOptions();

if (options.dsn) {
  Sentry.init(options);
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
