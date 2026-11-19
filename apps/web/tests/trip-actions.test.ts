import { beforeEach, describe, expect, it, vi } from "vitest";

import { deleteTrip } from "@/components/trips/actions";

import { TRIP_ID } from "./trip-fixture";

const del = vi.fn();
vi.mock("@/lib/session-api", () => ({
  requireSessionApi: async () => ({ DELETE: del }),
}));
const revalidatePath = vi.fn();
vi.mock("next/cache", () => ({ revalidatePath: (p: string) => revalidatePath(p) }));
const redirect = vi.fn((to: string) => {
  throw new Error(`REDIRECT ${to}`);
});
vi.mock("next/navigation", () => ({ redirect: (to: string) => redirect(to) }));

describe("deleteTrip", () => {
  beforeEach(() => {
    del.mockReset();
    revalidatePath.mockReset();
    redirect.mockClear();
  });

  it("rejects a non-UUID without calling the API", async () => {
    expect(await deleteTrip("nope")).toEqual({ error: "That trip couldn't be found." });
    expect(del).not.toHaveBeenCalled();
  });

  it.each([204, 404])("redirects to Saved trips on %i", async (status) => {
    del.mockResolvedValue({ response: { status } });
    await expect(deleteTrip(TRIP_ID)).rejects.toThrow("REDIRECT /trips/saved");
    expect(del).toHaveBeenCalledWith("/api/v1/trips/{trip_id}", {
      params: { path: { trip_id: TRIP_ID } },
    });
    expect(revalidatePath).toHaveBeenCalledWith("/trips/saved");
  });

  it("returns an error when the API fails or is unreachable", async () => {
    del.mockResolvedValue({ response: { status: 500 } });
    expect(await deleteTrip(TRIP_ID)).toEqual({
      error: "Couldn't delete the trip. Please try again.",
    });
    del.mockRejectedValue(new Error("network"));
    expect(await deleteTrip(TRIP_ID)).toEqual({
      error: "Couldn't delete the trip. Please try again.",
    });
    expect(redirect).not.toHaveBeenCalled();
  });
});
