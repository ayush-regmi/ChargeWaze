"use client";

import { useSyncExternalStore } from "react";

import { formatClock, formatDay, formatStartWindow } from "./observation-format";

type Times = {
  start_time: string;
  start_time_latest: string;
  start_time_is_exact: boolean;
  end_time: string;
};

const subscribe = () => () => {};

/** Text for the viewer's time zone (`undefined`) or UTC on the server render. */
export function driveTimesText(leg: Times, timeZone?: string): string {
  const day = formatDay(leg.end_time, timeZone);
  return `${day} · started ${formatStartWindow(leg, timeZone)} · ended ${formatClock(leg.end_time, timeZone)}`;
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
