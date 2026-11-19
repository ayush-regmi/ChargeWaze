import { beforeEach, describe, expect, it, vi } from "vitest";

import { planTrip, saveTrip } from "@/app/(app)/trips/_components/plan-actions";

import { feasiblePlan, TRIP_ID, VEHICLE_ID } from "./trip-fixture";

const post = vi.fn();
const session = vi.hoisted(() => ({ value: { access_token: "t" } as { access_token: string } | null }));
vi.mock("@/lib/supabase/server", () => ({
  createClient: async () => ({
    auth: { getSession: async () => ({ data: { session: session.value } }) },
  }),
}));
vi.mock("@/lib/api", () => ({ createApi: () => ({ POST: post }) }));

const INPUT = {
  vehicleId: VEHICLE_ID,
  origin: { lat: 39.38, lon: -77.4, label: "Tesla Supercharger" },
  destination: { lat: 43.65, lon: -79.38, label: "  Toronto  " },
  routeIndex: 1,
};

beforeEach(() => {
  post.mockReset();
  session.value = { access_token: "t" };
});

describe("planTrip", () => {
  it("sends endpoints and the route index only, and returns the plan", async () => {
    const plan = feasiblePlan();
    post.mockResolvedValue({ data: plan, response: { status: 200 } });
    expect(await planTrip(INPUT)).toEqual({ kind: "plan", plan });
    expect(post).toHaveBeenCalledWith("/api/v1/routes/plan", {
      body: {
        vehicle_id: VEHICLE_ID,
        origin: { lat: 39.38, lon: -77.4, label: "Tesla Supercharger" },
        destination: { lat: 43.65, lon: -79.38, label: "Toronto" },
        route_index: 1,
      },
    });
  });

  it("adds preferences and the starting SoC override when given", async () => {
    post.mockResolvedValue({ data: feasiblePlan(), response: { status: 200 } });
    await planTrip({ ...INPUT, preferences: { reserve_soc: 0.5 }, startingSocOverride: 0 });
    expect(post.mock.calls[0][1].body).toMatchObject({
      preferences: { reserve_soc: 0.5 },
      starting_soc_override: 0,
    });
  });

  it("maps a 422 STARTING_SOC_REQUIRED to the prompt outcome", async () => {
    post.mockResolvedValue({
      error: {
        error: {
          code: "VALIDATION_ERROR",
          message: "x",
          details: { reason: "STARTING_SOC_REQUIRED", last_soc: 0.64, last_recorded_at: null },
        },
      },
      response: { status: 422 },
    });
    expect(await planTrip(INPUT)).toEqual({
      kind: "soc_required",
      lastSoc: 0.64,
      lastRecordedAt: null,
    });
  });

  it("rejects bad input without calling the API", async () => {
    expect(await planTrip({ ...INPUT, routeIndex: -1 })).toEqual({
      kind: "error",
      code: "VALIDATION_ERROR",
    });
    expect(await planTrip({ ...INPUT, origin: { lat: 91, lon: 0 } })).toMatchObject({
      kind: "error",
    });
    expect(await planTrip({ ...INPUT, startingSocOverride: 1.2 })).toMatchObject({
      kind: "error",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("needs a session and survives a network failure", async () => {
    session.value = null;
    expect(await planTrip(INPUT)).toEqual({ kind: "error", code: "UNAUTHENTICATED" });
    session.value = { access_token: "t" };
    post.mockRejectedValue(new Error("down"));
    expect(await planTrip(INPUT)).toEqual({ kind: "error", code: "UNKNOWN" });
  });
});

describe("saveTrip", () => {
  it("saves by id", async () => {
    post.mockResolvedValue({ data: { id: TRIP_ID }, response: { status: 200 } });
    expect(await saveTrip(TRIP_ID)).toEqual({ ok: true });
    expect(post).toHaveBeenCalledWith("/api/v1/trips/{trip_id}/save", {
      params: { path: { trip_id: TRIP_ID } },
    });
  });

  it("reports not found, sign-out and other failures", async () => {
    expect(await saveTrip("nope")).toEqual({ ok: false, error: "NOT_FOUND" });
    post.mockResolvedValue({ response: { status: 404 } });
    expect(await saveTrip(TRIP_ID)).toEqual({ ok: false, error: "NOT_FOUND" });
    post.mockResolvedValue({ response: { status: 500 } });
    expect(await saveTrip(TRIP_ID)).toEqual({ ok: false, error: "UNKNOWN" });
    session.value = null;
    expect(await saveTrip(TRIP_ID)).toEqual({ ok: false, error: "UNAUTHENTICATED" });
  });
});
