"use client";

import { useSyncExternalStore } from "react";

/**
 * A timestamp in the viewer's own time zone. The server doesn't know it, so
 * the server render (and hydration) shows UTC and the client then switches to
 * local time, without a hydration mismatch.
 */
export function formatUtc(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function formatLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const subscribe = () => () => {};

export function LocalTime({ iso }: { iso: string }) {
  const text = useSyncExternalStore(
    subscribe,
    () => formatLocal(iso),
    () => formatUtc(iso),
  );
  return <time dateTime={iso}>{text}</time>;
}
