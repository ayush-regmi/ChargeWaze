import { beforeEach, describe, expect, it, vi } from "vitest";

import { retrievePlace, suggestPlaces } from "@/components/places/actions";

const get = vi.fn();
vi.mock("@/lib/supabase/server", () => ({
  createClient: async () => ({
    auth: { getSession: async () => ({ data: { session: { access_token: "t" } } }) },
  }),
}));
vi.mock("@/lib/api", () => ({ createApi: () => ({ GET: get }) }));

const SESSION_TOKEN = "0b8f6a2e-4c1d-4e7a-9f3b-2d5c6e7f8a9b";
const rateLimited = (details: Record<string, unknown>) => ({
  error: { error: { code: "RATE_LIMITED", message: "x", details } },
  response: { status: 429 },
});

beforeEach(() => {
  get.mockReset();
});

describe("suggestPlaces", () => {
  it("maps the per-user suggest limit to rate_limited with its retry hint", async () => {
    get.mockResolvedValue(rateLimited({ limit: "suggest", retry_after_seconds: 42 }));
    expect(await suggestPlaces({ q: "Toronto", sessionToken: SESSION_TOKEN })).toEqual({
      error: "rate_limited",
      retryAfterSeconds: 42,
    });
  });

  it("keeps rate_limited without a usable hint", async () => {
    get.mockResolvedValue(rateLimited({ limit: "suggest" }));
    expect(await suggestPlaces({ q: "Toronto", sessionToken: SESSION_TOKEN })).toEqual({
      error: "rate_limited",
      retryAfterSeconds: null,
    });
  });

  it("still reports other failures as unavailable", async () => {
    get.mockResolvedValue({
      error: { error: { code: "MAPBOX_UNREACHABLE", message: "x", details: null } },
      response: { status: 502 },
    });
    expect(await suggestPlaces({ q: "Toronto", sessionToken: SESSION_TOKEN })).toEqual({
      error: "unavailable",
    });
  });
});

describe("retrievePlace", () => {
  it("maps a 404 to not_found", async () => {
    get.mockResolvedValue({ error: { error: { code: "NOT_FOUND" } }, response: { status: 404 } });
    expect(await retrievePlace({ id: "abc", sessionToken: SESSION_TOKEN })).toEqual({
      error: "not_found",
    });
  });
});
