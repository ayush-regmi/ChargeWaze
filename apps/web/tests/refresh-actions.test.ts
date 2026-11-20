import { beforeEach, describe, expect, it, vi } from "vitest";

import { refreshVehicle } from "@/components/dashboard/refresh-actions";

const post = vi.fn();
vi.mock("@/lib/session-api", () => ({
  requireSessionApi: async () => ({ POST: post }),
}));
const revalidatePath = vi.fn();
vi.mock("next/cache", () => ({ revalidatePath: (p: string) => revalidatePath(p) }));

const ID = "0b6c1f7e-2d3a-4c5b-8e9f-0a1b2c3d4e5f";

function ok(inserted: number, isStale: boolean, recordedAt: string | null = "2026-11-19T15:00:00Z") {
  return {
    response: { status: 200 },
    data: {
      refreshed_at: "2026-11-19T15:02:00Z",
      inserted,
      next_refresh_at: "2026-11-19T15:17:00Z",
      state: { is_stale: isStale, recorded_at: recordedAt },
    },
  };
}

function failure(status: number, code?: string, retryAfter?: number) {
  return {
    response: { status },
    error: code
      ? { error: { code, details: retryAfter ? { retry_after_seconds: retryAfter } : {} } }
      : undefined,
  };
}

describe("refreshVehicle", () => {
  beforeEach(() => {
    post.mockReset();
    revalidatePath.mockReset();
  });

  it("rejects a non-UUID without calling the API", async () => {
    expect(await refreshVehicle("nope")).toEqual({
      ok: false,
      error: "This vehicle couldn't be found.",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("refreshes and revalidates the pages that show vehicle state", async () => {
    post.mockResolvedValue(ok(4, false));
    expect(await refreshVehicle(ID)).toEqual({
      ok: true,
      inserted: 4,
      isStale: false,
      recordedAt: "2026-11-19T15:00:00Z",
    });
    expect(post).toHaveBeenCalledWith("/api/v1/vehicles/{vehicle_id}/refresh", {
      params: { path: { vehicle_id: ID } },
    });
    expect(revalidatePath).toHaveBeenCalledWith("/dashboard");
    expect(revalidatePath).toHaveBeenCalledWith("/garage");
    expect(revalidatePath).toHaveBeenCalledWith(`/garage/${ID}`);
  });

  it("passes on that nothing newer arrived", async () => {
    post.mockResolvedValue(ok(0, true));
    expect(await refreshVehicle(ID)).toMatchObject({ ok: true, inserted: 0, isStale: true });
  });

  it.each([
    [failure(429, "RATE_LIMITED", 600), "This car was refreshed recently. You can refresh again in 10 min."],
    [failure(429, "RATE_LIMITED", 3600 * 5), "This car was refreshed recently. You can refresh again in 5 h."],
    [failure(429, "RATE_LIMITED"), "This car was refreshed recently. You can refresh again later."],
    [failure(429, "SMARTCAR_RATE_LIMITED", 120), "Your car's data provider is busy. Try again in 2 min."],
    [failure(429, "SMARTCAR_RATE_LIMITED"), "Your car's data provider is busy. Try again in a few minutes."],
    [failure(409, "VEHICLE_NOT_CONNECTED"), "This car isn't connected. Reconnect it in the Garage to refresh."],
    [failure(404, "NOT_FOUND"), "This vehicle couldn't be found."],
    [failure(502, "SMARTCAR_ERROR"), "Couldn't reach your car's data provider. Please try again later."],
    [failure(504, "SMARTCAR_UNREACHABLE"), "Couldn't reach your car's data provider. Please try again later."],
  ])("explains %# without revalidating", async (response, message) => {
    post.mockResolvedValue(response);
    expect(await refreshVehicle(ID)).toEqual({ ok: false, error: message });
    expect(revalidatePath).not.toHaveBeenCalled();
  });

  it("treats a network failure like an unreachable provider", async () => {
    post.mockRejectedValue(new Error("network"));
    expect(await refreshVehicle(ID)).toEqual({
      ok: false,
      error: "Couldn't reach your car's data provider. Please try again later.",
    });
  });
});
