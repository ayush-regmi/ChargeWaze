"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import type { StationsErrorCode } from "@/lib/stations";

const ERROR_TEXT: Record<StationsErrorCode, string> = {
  STATIONS_UNREACHABLE: "The station provider isn't responding right now.",
  STATIONS_NOT_CONFIGURED: "Station search isn't set up on this server yet.",
  VALIDATION_ERROR: "Some filters weren't accepted. Reset them and try again.",
  UNKNOWN: "Couldn't load stations.",
};

/** Error state (PRD §6, E6) with a retry. */
export function StationsError({
  code,
  onRetry,
  onReset,
}: {
  code: StationsErrorCode;
  onRetry: () => void;
  onReset?: () => void;
}) {
  return (
    <div
      role="alert"
      className="space-y-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm dark:border-red-900 dark:bg-red-950/40"
    >
      <p className="text-red-700 dark:text-red-300">{ERROR_TEXT[code]}</p>
      <div className="flex gap-2">
        <Button type="button" size="sm" variant="outline" onClick={onRetry}>
          Try again
        </Button>
        {code === "VALIDATION_ERROR" && onReset ? (
          <Button type="button" size="sm" variant="ghost" onClick={onReset}>
            Reset filters
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function StationsEmpty({ children }: { children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-600 dark:border-neutral-700 dark:text-neutral-400">
      {children ?? "No stations match these filters. Try a larger radius or fewer filters."}
    </div>
  );
}

export function StationsLoading() {
  return (
    <p role="status" className="text-sm text-neutral-500">
      Loading stations…
    </p>
  );
}
