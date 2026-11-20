import { connection } from "next/server";
import { Suspense } from "react";

import { StationsLoading } from "@/components/stations/station-states";
import type { ApiClient } from "@/lib/api";
import { requireSessionApi } from "@/lib/session-api";
import {
  STATIONS_PAGE_LIMIT,
  parseFilters,
  serializeFilters,
  stationsErrorCode,
  toStationsQuery,
  vehicleConnectorTypes,
  type StationsErrorCode,
} from "@/lib/stations";
import type { UnitSystem } from "@/lib/units";
import { pickDashboardVehicle, vehicleTitle } from "@/lib/vehicles";

import { parseChargingView, withView, type FavoriteStation } from "./_components/charging-tab";
import { ChargingView, type ChargingResult, type ChargingVehicle } from "./_components/charging-view";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default function ChargingPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Charging Stations</h1>
        <p className="text-sm text-muted-foreground">
          Find chargers near a place. Live availability is shown only when the provider reports
          it.
        </p>
      </div>
      <Suspense fallback={<StationsLoading />}>
        <Charging searchParams={searchParams} />
      </Suspense>
    </section>
  );
}

/** The vehicle whose connectors are the default filter: the Dashboard's one. */
async function loadVehicle(
  api: ApiClient,
  primaryVehicleId: string | null | undefined,
): Promise<ChargingVehicle | null> {
  const { data: vehicles } = await api.GET("/api/v1/vehicles");
  const vehicle = vehicles ? pickDashboardVehicle(vehicles.items, primaryVehicleId) : null;
  if (!vehicle) return null;
  const { data: detail } = await api.GET("/api/v1/vehicles/{vehicle_id}", {
    params: { path: { vehicle_id: vehicle.id } },
  });
  return {
    id: vehicle.id,
    title: vehicleTitle(vehicle),
    connectors: vehicleConnectorTypes(detail?.specs?.connectors),
  };
}

async function Charging({ searchParams }: { searchParams: SearchParams }) {
  await connection();
  const params = await searchParams;
  const filters = parseFilters(params);
  const view = parseChargingView(params);
  const qs = serializeFilters(filters);
  const api = await requireSessionApi(withView(qs ? `/charging?${qs}` : "/charging", view));

  const { data: me } = await api.GET("/api/v1/me");
  const units: UnitSystem = me?.preferences.units ?? "imperial";
  const vehicle = await loadVehicle(api, me?.preferences.primary_vehicle_id);

  const query = toStationsQuery(filters, {
    vehicleId: vehicle?.id ?? null,
    vehicleConnectors: vehicle?.connectors ?? [],
    units,
  });

  // Favorites load alongside the search: the Nearby cards need them for
  // their stars, the Favorites tab lists them. Null when they failed to load.
  const favoritesRequest = loadFavorites(api, vehicle?.id ?? null);

  let result: ChargingResult | null = null;
  let error: StationsErrorCode | null = null;
  if (query && view === "nearby") {
    try {
      const { data, error: body } = await api.GET("/api/v1/stations", {
        params: { query },
      });
      if (data) {
        result = {
          items: data.items,
          more: data.next_cursor !== null || data.items.length >= STATIONS_PAGE_LIMIT,
          unknownPowerCount: data.unknown_power_count,
        };
      } else {
        error = stationsErrorCode(body);
      }
    } catch {
      error = "STATIONS_UNREACHABLE";
    }
  }

  const favorites = await favoritesRequest;

  const networkOptions = [
    ...new Set([
      ...(me?.preferences.preferred_networks ?? []),
      ...(result?.items.map((s) => s.network).filter((n): n is string => Boolean(n)) ?? []),
    ]),
  ];

  return (
    <ChargingView
      filters={filters}
      units={units}
      vehicle={vehicle}
      result={result}
      error={error}
      networkOptions={networkOptions}
      view={view}
      favorites={favorites}
    />
  );
}

async function loadFavorites(
  api: ApiClient,
  vehicleId: string | null,
): Promise<FavoriteStation[] | null> {
  try {
    const { data } = await api.GET("/api/v1/stations/favorites", {
      params: { query: vehicleId ? { vehicle_id: vehicleId } : {} },
    });
    return data?.items ?? null;
  } catch {
    return null;
  }
}
