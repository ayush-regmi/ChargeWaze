"use client";

import { useSyncExternalStore } from "react";

import { dayKey, formatClock, formatDay, formatStartWindow } from "./observation-format";

type Times = {
  start_time: string;
  start_time_latest: string;
  start_time_is_exact: boolean;
  end_time: string;
};

const subscribe = () => () => {};

/**
 * Text for the viewer's time zone (`undefined`) or UTC on the server render.
 *
 * Usually "Nov 10 · started 07:12 · ended 08:41". The leading date is the
 * day the start falls on, so a drive that crosses midnight never puts its
 * start on the end's day: "Nov 9 · started between 22:00 and 23:30 · ended
 * Nov 10, 00:30". When the start bounds themselves span days, the window
 * already dates both ends.
 */
export function driveTimesText(leg: Times, timeZone?: string, now: Date = new Date()): string {
  const start = new Date(leg.start_time);
  const latest = new Date(leg.start_time_latest);
  const end = new Date(leg.end_time);
  const window = formatStartWindow(leg, timeZone, now);
  const endClock = formatClock(leg.end_time, timeZone);
  if ([start, latest, end].some((d) => Number.isNaN(d.getTime()))) {
    return `${formatDay(leg.end_time, timeZone, now)} · started ${window} · ended ${endClock}`;
  }
  const startDay = dayKey(start, timeZone);
  const latestDay = dayKey(latest, timeZone);
  const endDay = dayKey(end, timeZone);
  if (startDay !== latestDay) {
    // The window carries both dates; the end shares the latest bound's day or gets its own.
    const endText =
      latestDay === endDay ? endClock : `${formatDay(leg.end_time, timeZone, now)}, ${endClock}`;
    return `${formatDay(leg.end_time, timeZone, now)} · started ${window} · ended ${endText}`;
  }
  if (startDay === endDay) {
    return `${formatDay(leg.end_time, timeZone, now)} · started ${window} · ended ${endClock}`;
  }
  return (
    `${formatDay(leg.start_time, timeZone, now)} · started ${window}` +
    ` · ended ${formatDay(leg.end_time, timeZone, now)}, ${endClock}`
  );
}

export function driveEndText(endTime: string, timeZone?: string): string {
  return `ended ${formatDay(endTime, timeZone)}, ${formatClock(endTime, timeZone)}`;
}

function useLocalText(local: () => string, utc: () => string): string {
  return useSyncExternalStore(subscribe, local, utc);
}

/**
 * When an observed drive happened, in the viewer's own time zone. The server
 * doesn't know that zone, so the server render (and hydration) shows UTC and
 * the client then switches, like `LocalTime`. A bounded start is shown as a
 * range (§3.18 Times), never as one exact time.
 */
export function DriveTimes({ leg }: { leg: Times }) {
  const text = useLocalText(
    () => driveTimesText(leg),
    () => `${driveTimesText(leg, "UTC")} (UTC)`,
  );
  return (
    <time dateTime={leg.end_time} data-testid="drive-times">
      {text}
    </time>
  );
}

/** Only the end of a drive: the observation legs in §3.18e carry no start bound. */
export function DriveEnd({ endTime }: { endTime: string }) {
  const text = useLocalText(
    () => driveEndText(endTime),
    () => `${driveEndText(endTime, "UTC")} (UTC)`,
  );
  return <time dateTime={endTime}>{text}</time>;
}
