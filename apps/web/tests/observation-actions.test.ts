import { beforeEach, describe, expect, it, vi } from "vitest";

import { confirmObservedTrip, rejectObservedTrip } from "@/components/trips/observation-actions";

import { LEG_1, TRIP_ID } from "./observation-fixture";

const post = vi.fn();
vi.mock("@/lib/session-api", () => ({
  requireSessionApi: async () => ({ POST: post }),
}));
const revalidatePath = vi.fn();
vi.mock("next/cache", () => ({ revalidatePath: (p: string) => revalidatePath(p) }));

describe("observed-trip review actions", () => {
  beforeEach(() => {
    post.mockReset();
    revalidatePath.mockReset();
  });

  it("rejects a non-UUID without calling the API", async () => {
    expect(await confirmObservedTrip("nope")).toEqual({ error: "This drive couldn't be found." });
    expect(post).not.toHaveBeenCalled();
  });

  it("confirms and revalidates the list and the matched plan", async () => {
    post.mockResolvedValue({
      response: { status: 200 },
      data: { review_status: "confirmed", planned_trip_id: TRIP_ID },
    });
    expect(await confirmObservedTrip(LEG_1)).toEqual({ status: "confirmed" });
    expect(post).toHaveBeenCalledWith("/api/v1/observed-trips/{trip_id}/confirm", {
      params: { path: { trip_id: LEG_1 } },
    });
    expect(revalidatePath).toHaveBeenCalledWith("/trips/saved");
    expect(revalidatePath).toHaveBeenCalledWith(`/trips/${TRIP_ID}`);
  });

  it("rejects through its own endpoint", async () => {
    post.mockResolvedValue({
      response: { status: 200 },
      data: { review_status: "rejected", planned_trip_id: null },
    });
    expect(await rejectObservedTrip(LEG_1)).toEqual({ status: "rejected" });
    expect(post).toHaveBeenCalledWith("/api/v1/observed-trips/{trip_id}/reject", {
      params: { path: { trip_id: LEG_1 } },
    });
    expect(revalidatePath).toHaveBeenCalledTimes(1);
  });

  it("explains a 404 and other failures without revalidating", async () => {
    post.mockResolvedValue({ response: { status: 404 } });
    expect(await rejectObservedTrip(LEG_1)).toEqual({
      error: "This drive no longer exists. Refresh the list.",
    });
    post.mockResolvedValue({ response: { status: 500 } });
    expect(await confirmObservedTrip(LEG_1)).toEqual({
      error: "Couldn't confirm this drive. Please try again.",
    });
    post.mockRejectedValue(new Error("network"));
    expect(await rejectObservedTrip(LEG_1)).toEqual({
      error: "Couldn't reject this drive. Please try again.",
    });
    expect(revalidatePath).not.toHaveBeenCalled();
  });
});
