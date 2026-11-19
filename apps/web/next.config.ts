import { withSentryConfig } from "@sentry/nextjs/config";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  async redirects() {
    return [{ source: "/", destination: "/dashboard", permanent: false }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

// Sentry build step. Source maps are uploaded (and a release created) only when
// SENTRY_AUTH_TOKEN is set, e.g. in the production build; local builds and CI
// make no calls to Sentry. The token is build-time only and never NEXT_PUBLIC_.
const sentryUpload = Boolean(process.env.SENTRY_AUTH_TOKEN);

export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  telemetry: false,
  silent: !sentryUpload,
  sourcemaps: { disable: !sentryUpload },
  release: { create: sentryUpload, finalize: sentryUpload },
});
