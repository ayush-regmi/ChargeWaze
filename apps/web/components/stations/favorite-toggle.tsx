"use client";

import { useState, useTransition, type MouseEvent } from "react";

import { cn } from "@/lib/utils";

import { setStationFavorite } from "./favorite-actions";

function StarIcon({ filled }: { filled: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 20 20"
      className="size-5"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={1.5}
    >
      <path
        strokeLinejoin="round"
        d="M10 2.5l2.32 4.7 5.18.75-3.75 3.66.89 5.16L10 14.33l-4.64 2.44.89-5.16L2.5 7.95l5.18-.75L10 2.5z"
      />
    </svg>
  );
}

/**
 * Star toggle for a station (phase3_plan.md F8). Flips at once, then saves;
 * if saving fails it flips back and shows why. `onChange` runs after a
 * successful save (the Favorites tab uses it to refresh its list).
 */
export function FavoriteToggle({
  stationId,
  stationName,
  favorite,
  onChange,
  className,
}: {
  stationId: string;
  stationName: string;
  favorite: boolean;
  onChange?: (favorite: boolean) => void;
  className?: string;
}) {
  const [on, setOn] = useState(favorite);
  // A new `favorite` from the server (after a refresh) wins over local state.
  const [seen, setSeen] = useState(favorite);
  if (favorite !== seen) {
    setSeen(favorite);
    setOn(favorite);
  }
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function toggle(e: MouseEvent) {
    // Cards select a station on click; starring shouldn't.
    e.stopPropagation();
    const next = !on;
    setOn(next);
    setError(null);
    startTransition(async () => {
      const result = await setStationFavorite(stationId, next);
      if (result?.error) {
        setOn(!next);
        setError(result.error);
      } else {
        onChange?.(next);
      }
    });
  }

  return (
    <div className={cn("flex flex-col items-end gap-1", className)}>
      <button
        type="button"
        aria-pressed={on}
        aria-label={on ? `Remove ${stationName} from favorites` : `Add ${stationName} to favorites`}
        title={on ? "Remove from favorites" : "Add to favorites"}
        disabled={pending}
        onClick={toggle}
        className={cn(
          "rounded-md p-1 transition-colors hover:bg-neutral-100 disabled:opacity-60 dark:hover:bg-neutral-800",
          on ? "text-amber-500" : "text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300",
        )}
      >
        <StarIcon filled={on} />
      </button>
      {error ? (
        <p role="alert" className="max-w-48 text-right text-xs text-red-700 dark:text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}
