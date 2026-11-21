import { createClient, type Client, type paths } from "@chargewaze/api-client";

import { env } from "@/lib/env";

export type ApiClient = Client<paths>;

/**
 * Build a typed ChargeWaze API client bound to the current request's Supabase
 * access token (if any). Uses ``openapi-fetch`` with the generated ``paths``
 * type so GET/POST/etc are strongly typed against the FastAPI schema.
 */
export function createApi(options: { accessToken?: string | null } = {}): ApiClient {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (options.accessToken) {
    headers["Authorization"] = `Bearer ${options.accessToken}`;
  }
  return createClient<paths>({ baseUrl: env.apiBaseUrl, headers });
}
