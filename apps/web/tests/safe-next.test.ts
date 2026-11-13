import { describe, expect, it } from "vitest";

import { DEFAULT_NEXT_PATH, safeNextPath } from "@/lib/safe-next";

describe("safeNextPath", () => {
  it.each([
    ["/dashboard", "/dashboard"],
    ["/trips?id=42", "/trips?id=42"],
    ["/profile#prefs", "/profile#prefs"],
    ["/charging/../garage", "/garage"],
  ])("keeps same-origin path %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it.each([
    null,
    undefined,
    "",
    "dashboard",
    "//evil.example",
    "//evil.example/dashboard",
    "/\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "https://evil.example",
    "http:evil.example",
    "javascript:alert(1)",
    // Dot segments that normalize into a protocol-relative URL.
    "/.//evil.example",
    "/x/..//evil.example",
    "/%2e//evil.example",
    "/./\\evil.example",
    "/.//evil.example/path?q=1#h",
  ])("rejects %j", (input) => {
    expect(safeNextPath(input)).toBe(DEFAULT_NEXT_PATH);
  });

  it.each([
    "/dashboard",
    "/trips?id=42",
    "/.//evil.example",
    "/x/..//evil.example",
    "//evil.example",
  ])("never returns a value that leaves the origin for %j", (input) => {
    const out = safeNextPath(input);
    expect(out.startsWith("//")).toBe(false);
    expect(new URL(out, "http://localhost:3000").origin).toBe("http://localhost:3000");
  });

  it("uses a custom fallback", () => {
    expect(safeNextPath("//evil.example", "/profile")).toBe("/profile");
  });
});
