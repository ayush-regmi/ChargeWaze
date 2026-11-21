/**
 * ChargeWaze typed API client.
 *
 * Re-exports ``openapi-fetch``'s ``createClient`` plus the generated
 * ``paths`` / ``components`` / ``operations`` types so the frontend has a
 * single import source for both.
 *
 * Usage:
 *
 *     import { createClient, type paths } from "@chargewaze/api-client";
 *     const api = createClient<paths>({ baseUrl: "http://localhost:8000" });
 *     const { data, error } = await api.GET("/api/v1/me");
 *
 * ``src/schema.d.ts`` is regenerated from the FastAPI OpenAPI document via
 * ``npm run generate --workspace=packages/api-client``.
 */

export type { paths, components, operations } from "./schema";
export { default as createClient } from "openapi-fetch";
export type { Client, ClientOptions } from "openapi-fetch";
