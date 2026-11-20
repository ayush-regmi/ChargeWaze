import { beforeEach, describe, expect, it, vi } from "vitest";

import { deleteAccount, recordPrivacyConsent } from "@/components/profile/privacy-actions";

const del = vi.fn();
const post = vi.fn();
vi.mock("@/lib/session-api", () => ({
  requireSessionApi: async () => ({ DELETE: del, POST: post }),
}));
const signOut = vi.fn();
vi.mock("@/lib/supabase/server", () => ({
  createClient: async () => ({ auth: { signOut } }),
}));
const revalidatePath = vi.fn();
vi.mock("next/cache", () => ({ revalidatePath: (p: string) => revalidatePath(p) }));
const redirect = vi.fn((to: string) => {
  throw new Error(`REDIRECT ${to}`);
});
vi.mock("next/navigation", () => ({ redirect: (to: string) => redirect(to) }));

describe("deleteAccount", () => {
  beforeEach(() => {
    del.mockReset();
    signOut.mockReset();
    redirect.mockClear();
  });

  it("refuses without the typed confirmation and never calls the API", async () => {
    expect(await deleteAccount("delete please")).toEqual({ error: "Type DELETE to confirm." });
    expect(del).not.toHaveBeenCalled();
  });

  it("deletes, signs out and goes to sign-in on 204", async () => {
    del.mockResolvedValue({ response: { status: 204 } });
    await expect(deleteAccount("DELETE")).rejects.toThrow("REDIRECT /sign-in");
    expect(del).toHaveBeenCalledWith("/api/v1/me");
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("still redirects if signing out fails (the auth user is already gone)", async () => {
    del.mockResolvedValue({ response: { status: 204 } });
    signOut.mockRejectedValue(new Error("user not found"));
    await expect(deleteAccount("DELETE")).rejects.toThrow("REDIRECT /sign-in");
  });

  it("keeps the user signed in and explains a failure", async () => {
    del.mockResolvedValue({ response: { status: 500 } });
    expect(await deleteAccount("DELETE")).toEqual({
      error: "Couldn't delete your account. Please try again.",
    });
    del.mockRejectedValue(new Error("network"));
    expect(await deleteAccount("DELETE")).toEqual({
      error: "Couldn't delete your account. Please try again.",
    });
    expect(signOut).not.toHaveBeenCalled();
    expect(redirect).not.toHaveBeenCalled();
  });
});

describe("recordPrivacyConsent", () => {
  beforeEach(() => {
    post.mockReset();
    revalidatePath.mockReset();
  });

  it("posts the answer and revalidates Profile", async () => {
    post.mockResolvedValue({ response: { status: 201 }, data: { granted: true } });
    expect(await recordPrivacyConsent("2026-11-19", true)).toEqual({ granted: true });
    expect(post).toHaveBeenCalledWith("/api/v1/me/consents", {
      body: { kind: "privacy_notice", version: "2026-11-19", granted: true },
    });
    expect(revalidatePath).toHaveBeenCalledWith("/profile");
  });

  it("explains a changed notice and other failures", async () => {
    post.mockResolvedValue({ response: { status: 422 } });
    expect(await recordPrivacyConsent("old", true)).toEqual({
      error: "The privacy notice has changed. Reload the page to see the new version.",
    });
    post.mockResolvedValue({ response: { status: 500 } });
    expect(await recordPrivacyConsent("2026-11-19", false)).toEqual({
      error: "Couldn't save your answer. Please try again.",
    });
    expect(revalidatePath).not.toHaveBeenCalled();
  });
});
