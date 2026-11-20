import { describe, expect, it } from "vitest";

import { retryAfterSeconds, tryAgainText } from "@/lib/retry-after";

describe("retryAfterSeconds", () => {
  it("reads a positive details.retry_after_seconds", () => {
    expect(retryAfterSeconds({ limit: "suggest", retry_after_seconds: 30 })).toBe(30);
  });

  it("ignores missing, non-numeric or non-positive values", () => {
    expect(retryAfterSeconds(null)).toBeNull();
    expect(retryAfterSeconds([])).toBeNull();
    expect(retryAfterSeconds({})).toBeNull();
    expect(retryAfterSeconds({ retry_after_seconds: "30" })).toBeNull();
    expect(retryAfterSeconds({ retry_after_seconds: 0 })).toBeNull();
    expect(retryAfterSeconds({ retry_after_seconds: Number.NaN })).toBeNull();
  });
});

describe("tryAgainText", () => {
  it("rounds up to whole minutes", () => {
    expect(tryAgainText(1)).toBe("Try again in 1 minute.");
    expect(tryAgainText(60)).toBe("Try again in 1 minute.");
    expect(tryAgainText(61)).toBe("Try again in 2 minutes.");
    expect(tryAgainText(89 * 60)).toBe("Try again in 89 minutes.");
  });

  it("switches to hours for long waits", () => {
    expect(tryAgainText(3 * 3600)).toBe("Try again in about 3 hours.");
  });

  it("falls back to a vague hint without a wait", () => {
    expect(tryAgainText(null)).toBe("Try again in a few minutes.");
    expect(tryAgainText(undefined)).toBe("Try again in a few minutes.");
    expect(tryAgainText(-5)).toBe("Try again in a few minutes.");
  });
});
