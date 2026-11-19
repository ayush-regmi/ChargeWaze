import Link from "next/link";

import { cn } from "@/lib/utils";
import {
  connectorDetail,
  connectorLabel,
  hasUnknownPower,
  type Station,
  type StationConnector,
} from "@/lib/stations";

import { FavoriteToggle } from "./favorite-toggle";

export function ConnectorChips({ connectors }: { connectors: readonly StationConnector[] }) {
  if (connectors.length === 0) {
    return <p className="text-xs text-neutral-500">Connector types not reported</p>;
  }
  return (
    <ul aria-label="Connectors" className="flex flex-wrap gap-1.5">
      {connectors.map((c) => (
        <li
          key={c.type}
          className="rounded-md bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
        >
          <span className="font-medium">{connectorLabel(c.type)}</span>{" "}
          <span>{connectorDetail(c)}</span>
        </li>
      ))}
    </ul>
  );
}

export function CompatibilityBadge({ compatible }: { compatible: boolean | null }) {
  if (compatible === null) return null;
  return compatible ? (
    <span className="rounded-md bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800 dark:bg-green-950 dark:text-green-300">
      Compatible
    </span>
  ) : (
    <span className="rounded-md bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400">
      Not compatible
    </span>
  );
}

export function PowerUnknownBadge() {
  return (
    <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300">
      Power unknown
    </span>
  );
}

/**
 * One station in a list. Shows only what the provider gave: connector counts
 * and power fall back to "unknown", and there is no availability badge
 * unless the API sends `live_availability` (always null in Phase 3).
 */
export function StationCard({
  station,
  href,
  distanceText = null,
  selected = false,
  onSelect,
  favorite,
  onFavoriteChange,
}: {
  station: Station;
  href: string;
  /** Pre-formatted distance in the user's units, or null to hide it. */
  distanceText?: string | null;
  selected?: boolean;
  onSelect?: (id: string) => void;
  /** Whether the user starred it. Leave undefined to hide the star (F8). */
  favorite?: boolean;
  onFavoriteChange?: (favorite: boolean) => void;
}) {
  const unknownPower = hasUnknownPower(station);
  const liveAvailability = (station as { live_availability?: unknown }).live_availability;
  return (
    <article
      aria-label={station.name}
      aria-current={selected || undefined}
      data-station-id={station.id}
      onClick={onSelect ? () => onSelect(station.id) : undefined}
      className={cn(
        "space-y-2 rounded-lg border bg-white p-3 shadow-sm dark:bg-neutral-900",
        selected
          ? "border-blue-500 ring-2 ring-blue-500/30"
          : "border-neutral-200 dark:border-neutral-800",
        onSelect && "cursor-pointer",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <h3 className="text-sm font-semibold">
            <Link href={href} className="hover:underline" onClick={(e) => e.stopPropagation()}>
              {station.name}
            </Link>
          </h3>
          <p className="text-xs text-neutral-600 dark:text-neutral-400">
            {station.network ?? "Network not reported"}
          </p>
        </div>
        <div className="flex shrink-0 items-start gap-1">
          {distanceText ? (
            <span className="pt-1.5 text-xs font-medium text-neutral-700 dark:text-neutral-300">
              {distanceText}
            </span>
          ) : null}
          {favorite !== undefined ? (
            <FavoriteToggle
              stationId={station.id}
              stationName={station.name}
              favorite={favorite}
              onChange={onFavoriteChange}
            />
          ) : null}
        </div>
      </div>
      <ConnectorChips connectors={station.connectors} />
      <div className="flex flex-wrap gap-1.5">
        <CompatibilityBadge compatible={station.compatible} />
        {unknownPower ? <PowerUnknownBadge /> : null}
        {liveAvailability !== null && liveAvailability !== undefined ? (
          <span className="rounded-md bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-950 dark:text-blue-300">
            Live availability reported
          </span>
        ) : null}
      </div>
    </article>
  );
}
