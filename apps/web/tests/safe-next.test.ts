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
  ])("rejects %j", (input) => {
    expect(safeNextPath(input)).toBe(DEFAULT_NEXT_PATH);
  });

  it("uses a custom fallback", () => {
    expect(safeNextPath("//evil.example", "/profile")).toBe("/profile");
  });
});
