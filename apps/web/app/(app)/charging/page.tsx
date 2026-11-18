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

import { ChargingView, type ChargingResult, type ChargingVehicle } from "./_components/charging-view";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default function ChargingPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Charging Stations</h1>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
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
  const filters = parseFilters(await searchParams);
  const qs = serializeFilters(filters);
  const api = await requireSessionApi(qs ? `/charging?${qs}` : "/charging");

  const { data: me } = await api.GET("/api/v1/me");
  const units: UnitSystem = me?.preferences.units ?? "imperial";
  const vehicle = await loadVehicle(api, me?.preferences.primary_vehicle_id);

  const query = toStationsQuery(filters, {
    vehicleId: vehicle?.id ?? null,
    vehicleConnectors: vehicle?.connectors ?? [],
    units,
  });

  let result: ChargingResult | null = null;
  let error: StationsErrorCode | null = null;
  if (query) {
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
    />
  );
}
