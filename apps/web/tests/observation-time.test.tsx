import { describe, expect, it } from "vitest";

import { driveTimesText } from "@/components/trips/observation-time";

const NOW = new Date("2026-11-19T12:00:00Z");

function leg(start: string, latest: string, end: string) {
  return {
    start_time: start,
    start_time_latest: latest,
    start_time_is_exact: start === latest,
    end_time: end,
  };
}

describe("driveTimesText", () => {
  it("keeps the same-day output unchanged", () => {
    expect(
      driveTimesText(
        leg("2026-11-10T07:12:00Z", "2026-11-10T08:00:00Z", "2026-11-10T08:41:00Z"),
        "UTC",
        NOW,
      ),
    ).toBe("Nov 10 · started between 07:12 and 08:00 · ended 08:41");
    expect(
      driveTimesText(
        leg("2026-11-10T08:02:00Z", "2026-11-10T08:02:00Z", "2026-11-10T08:41:00Z"),
        "UTC",
        NOW,
      ),
    ).toBe("Nov 10 · started 08:02 · ended 08:41");
  });

  it("dates the start when a bounded start is the day before the end", () => {
    expect(
      driveTimesText(
        leg("2026-11-09T22:00:00Z", "2026-11-09T23:30:00Z", "2026-11-10T00:30:00Z"),
        "UTC",
        NOW,
      ),
    ).toBe("Nov 9 · started between 22:00 and 23:30 · ended Nov 10, 00:30");
  });

  it("dates the start when an exact start is the day before the end", () => {
    expect(
      driveTimesText(
        leg("2026-11-09T23:50:00Z", "2026-11-09T23:50:00Z", "2026-11-10T00:30:00Z"),
        "UTC",
        NOW,
      ),
    ).toBe("Nov 9 · started 23:50 · ended Nov 10, 00:30");
  });

  it("uses the given time zone's days, not UTC's", () => {
    // 03:00Z–04:00Z on Nov 10 is 22:00–23:00 on Nov 9 in New York; the end
    // (05:30Z) is 00:30 on Nov 10 there.
    expect(
      driveTimesText(
        leg("2026-11-10T03:00:00Z", "2026-11-10T04:00:00Z", "2026-11-10T05:30:00Z"),
        "America/New_York",
        NOW,
      ),
    ).toBe("Nov 9 · started between 22:00 and 23:00 · ended Nov 10, 00:30");
    // The same instants are all on Nov 10 in UTC: no extra dates.
    expect(
      driveTimesText(
        leg("2026-11-10T03:00:00Z", "2026-11-10T04:00:00Z", "2026-11-10T05:30:00Z"),
        "UTC",
        NOW,
      ),
    ).toBe("Nov 10 · started between 03:00 and 04:00 · ended 05:30");
  });

  it("keeps the dated window when the start bounds span days (§3.18 example)", () => {
    expect(
      driveTimesText(
        leg("2026-11-09T18:12:00Z", "2026-11-10T08:41:00Z", "2026-11-10T08:41:00Z"),
        "UTC",
        NOW,
      ),
    ).toBe("Nov 10 · started between Nov 9, 18:12 and Nov 10, 08:41 · ended 08:41");
  });
});
