import { describe, expect, it } from "vitest";

import { connectResultUrl } from "@/lib/connect";

const ORIGIN = "http://app.test";

describe("connectResultUrl", () => {
  it("adds the connect flag to the path", () => {
    expect(connectResultUrl("/garage", "success", ORIGIN).toString()).toBe(
      "http://app.test/garage?connect=success",
    );
  });

  it("keeps the path's query and fragment and replaces an old flag", () => {
    const url = connectResultUrl("/garage?tab=cars&connect=old#top", "denied", ORIGIN);
    expect(url.pathname).toBe("/garage");
    expect(url.searchParams.get("tab")).toBe("cars");
    expect(url.searchParams.getAll("connect")).toEqual(["denied"]);
    expect(url.hash).toBe("#top");
  });

  it.each([null, undefined, "", "//evil.example", "https://evil.example/x", "/\\evil"])(
    "falls back to the Garage on our origin for %j",
    (path) => {
      const url = connectResultUrl(path, "error", ORIGIN);
      expect(url.origin).toBe(ORIGIN);
      expect(url.pathname).toBe("/garage");
    },
  );
});
