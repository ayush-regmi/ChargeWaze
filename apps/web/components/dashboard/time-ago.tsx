"use client";

import { useSyncExternalStore } from "react";

import { formatUtc } from "@/components/garage/local-time";

/** "5 minutes ago" style text for `iso` relative to `now` (ms since epoch). */
export function formatTimeAgo(iso: string, now: number): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (seconds < 60) return rtf.format(-seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return rtf.format(-minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (hours < 48) return rtf.format(-hours, "hour");
  return rtf.format(-Math.round(hours / 24), "day");
}

const subscribe = () => () => {};

/**
 * Relative time on the client. The server can't know "now" at view time, so
 * the server render (and hydration) shows the UTC timestamp instead.
 */
export function TimeAgo({ iso }: { iso: string }) {
  const text = useSyncExternalStore(
    subscribe,
    () => formatTimeAgo(iso, Date.now()),
    () => formatUtc(iso),
  );
  return <time dateTime={iso}>{text}</time>;
}
