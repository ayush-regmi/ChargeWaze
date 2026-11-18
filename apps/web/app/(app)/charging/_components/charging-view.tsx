"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";

import { DataAttribution } from "@/components/map/attribution";
import { LazyMapView } from "@/components/map/lazy-map-view";
import type { Place } from "@/components/places/actions";
import { PlaceSearch } from "@/components/places/place-search";
import { UseMyLocation } from "@/components/places/use-my-location";
import { StationCard } from "@/components/stations/station-card";
import { StationFilters } from "@/components/stations/station-filters";
import { StationsEmpty, StationsError } from "@/components/stations/station-states";
import { formatDistanceText } from "@/lib/geo-format";
import {
  EMPTY_FILTERS,
  serializeFilters,
  sortByDistance,
  type ChargingFilters,
  type ConnectorType,
  type Station,
  type StationsErrorCode,
} from "@/lib/stations";
import type { UnitSystem } from "@/lib/units";

export type ChargingVehicle = {
  id: string;
  title: string;
  /** The vehicle's connectors that stations use; empty when unknown. */
  connectors: ConnectorType[];
};

export type ChargingResult = {
  items: Station[];
  /** True when the provider had more stations than this page shows. */
  more: boolean;
  unknownPowerCount: number;
};

export function chargingHref(filters: ChargingFilters): string {
  const qs = serializeFilters(filters);
  return qs ? `/charging?${qs}` : "/charging";
}

/**
 * The interactive part of `/charging`: location source, filters, map and
 * list. Every change goes into the URL; the server page reloads the stations
 * for it, so a hard reload or a shared link shows the same results.
 */
export function ChargingView({
  filters,
  units,
  vehicle,
  result,
  error,
  networkOptions,
}: {
  filters: ChargingFilters;
  units: UnitSystem;
  vehicle: ChargingVehicle | null;
  result: ChargingResult | null;
  error: StationsErrorCode | null;
  networkOptions: string[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const go = (next: ChargingFilters, mode: "push" | "replace" = "replace") => {
    startTransition(() => {
      router[mode](chargingHref(next), { scroll: false });
    });
  };

  const center = filters.location;
  const rows = useMemo(
    () => (result && center ? sortByDistance(result.items, center) : []),
    [result, center],
  );
  const mapStations = useMemo(
    () =>
      rows.map(({ station }) => ({
        id: station.id,
        location: station.location,
        name: station.name,
        compatible: station.compatible,
      })),
    [rows],
  );
  const detailQuery = serializeFilters(filters);

  const selectStation = (id: string) => {
    setSelectedId(id);
    document
      .querySelector(`[data-station-id="${CSS.escape(id)}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  const onPlace = (place: Place) => {
    setSelectedId(null);
    go(
      { ...filters, location: { lat: place.location.lat, lon: place.location.lon, label: place.name } },
      "push",
    );
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <PlaceSearch
          key={center ? `${center.lat},${center.lon}` : "none"}
          label="Search near"
          near={center}
          initialValue={center?.label ?? ""}
          onSelect={onPlace}
          className="min-w-64 flex-1"
        />
        <UseMyLocation
          onLocate={(p) => {
            setSelectedId(null);
            go({ ...filters, location: { ...p, label: "My location" } }, "push");
          }}
        />
      </div>

      {vehicle === null ? (
        <p className="rounded-md bg-neutral-100 px-3 py-2 text-sm text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
          Showing every connector type.{" "}
          <Link href="/garage" className="font-medium underline-offset-2 hover:underline">
            Connect a vehicle
          </Link>{" "}
          to filter by its connectors and see which stations are compatible.
        </p>
      ) : vehicle.connectors.length === 0 ? (
        <p className="rounded-md bg-neutral-100 px-3 py-2 text-sm text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
          Your {vehicle.title}&apos;s connectors aren&apos;t set, so compatibility can&apos;t be
          checked.{" "}
          <Link
            href={`/garage/${vehicle.id}`}
            className="font-medium underline-offset-2 hover:underline"
          >
            Add them in the Garage
          </Link>
          .
        </p>
      ) : null}

      <StationFilters
        filters={filters}
        units={units}
        vehicleConnectors={vehicle?.connectors ?? []}
        networkOptions={networkOptions}
        onChange={(next) => go(next)}
      />

      {center === null ? (
        <StationsEmpty>
          Search for a place or use your location to find charging stations nearby.
        </StationsEmpty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <div className="h-80 lg:sticky lg:top-4 lg:h-[36rem]">
            <LazyMapView
              label="Charging stations map"
              center={center}
              stations={mapStations}
              selectedStationId={selectedId}
              onSelect={selectStation}
              className="h-full"
            />
          </div>
          <section aria-label="Stations" aria-busy={pending || undefined} className="space-y-3">
            {error ? (
              <StationsError
                code={error}
                onRetry={() => startTransition(() => router.refresh())}
                onReset={() => go({ ...EMPTY_FILTERS, location: center })}
              />
            ) : result ? (
              <>
                <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
                  {pending
                    ? "Updating…"
                    : `${rows.length} ${rows.length === 1 ? "station" : "stations"}${
                        center.label ? ` near ${center.label}` : ""
                      }`}
                  {!pending && result.unknownPowerCount > 0
                    ? ` · ${result.unknownPowerCount} with unknown power`
                    : null}
                </p>
                {result.more ? (
                  <p className="text-xs text-neutral-500">
                    Showing the nearest {rows.length}. Narrow the filters or the radius to see
                    others.
                  </p>
                ) : null}
                {rows.length === 0 ? (
                  <StationsEmpty />
                ) : (
                  <ol className="space-y-2">
                    {rows.map(({ station, distanceKm }) => (
                      <li key={station.id}>
                        <StationCard
                          station={station}
                          href={`/charging/${station.id}${detailQuery ? `?${detailQuery}` : ""}`}
                          distanceText={formatDistanceText(distanceKm, units)}
                          selected={station.id === selectedId}
                          onSelect={setSelectedId}
                        />
                      </li>
                    ))}
                  </ol>
                )}
              </>
            ) : null}
            <DataAttribution />
          </section>
        </div>
      )}
    </div>
  );
}
