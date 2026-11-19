"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { isValidTimeZone } from "./params";

/** The browser's IANA time zone, or null when it can't be read. */
export function browserTimeZone(): string | null {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return tz && isValidTimeZone(tz) ? tz : null;
  } catch {
    return null;
  }
}

/**
 * The server can't know the viewer's time zone, so buckets start in UTC until
 * this puts the browser's zone into the URL (`tz`), once, without adding a
 * history entry. An explicit `tz` in the URL is kept.
 */
export function TzSync() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const current = params.get("tz");

  useEffect(() => {
    if (current) return;
    const tz = browserTimeZone();
    if (!tz) return;
    const next = new URLSearchParams(params.toString());
    next.set("tz", tz);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  }, [current, params, pathname, router]);

  return null;
}
