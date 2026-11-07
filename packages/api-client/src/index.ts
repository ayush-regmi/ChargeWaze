/**
 * Chargewise typed API client.
 *
 * The real `openapi-fetch` client is created by calling `createApiClient`.
 * `src/schema.d.ts` is regenerated from the FastAPI OpenAPI document via
 * `npm run generate --workspace=packages/api-client`. Until it has been
 * generated at least once, the type parameter falls back to `unknown` so the
 * client is usable but untyped.
 */

import createClient, { type Client, type ClientOptions } from "openapi-fetch";

import type { paths } from "./schema";

export type { paths, components, operations } from "./schema";

type SchemaType = paths;

export interface CreateApiClientOptions extends ClientOptions {
  /** Base URL of the FastAPI backend, e.g. `http://localhost:8000`. */
  baseUrl: string;
  /** Optional bearer token (Supabase session access token). */
  accessToken?: string;
}

export function createApiClient(
  options: CreateApiClientOptions,
): Client<SchemaType> {
  const { accessToken, headers, ...rest } = options;
  const mergedHeaders: Record<string, string> = {
    "Content-Type": "application/json",
    ...(headers as Record<string, string> | undefined),
  };
  if (accessToken) {
    mergedHeaders["Authorization"] = `Bearer ${accessToken}`;
  }
  return createClient<SchemaType>({
    ...rest,
    headers: mergedHeaders,
  });
}

export type ApiClient = ReturnType<typeof createApiClient>;
