"use client";

import { createBrowserClient } from "@supabase/ssr";

import { env } from "@/lib/env";

/**
 * Supabase client for Client Components and browser event handlers. Uses the
 * Next.js-aware ``createBrowserClient`` so that session cookies set by the
 * server callback are visible here.
 */
export function createClient() {
  return createBrowserClient(env.supabaseUrl, env.supabaseAnonKey);
}
