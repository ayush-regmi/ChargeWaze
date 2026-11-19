// Sentry for the Node.js server runtime. Loaded by instrumentation.ts.
import * as Sentry from "@sentry/nextjs";

import { sharedSentryOptions } from "./sentry.shared.config";

const options = sharedSentryOptions();

if (options.dsn) {
  Sentry.init(options);
}
