import { connection } from "next/server";
import { Suspense } from "react";

import type { ApiClient } from "@/lib/api";
import { requireSessionApi } from "@/lib/session-api";
import { vehicleConnectorTypes } from "@/lib/stations";
import { parseTripState, tripHref } from "@/lib/trip-url";
import type { UnitSystem } from "@/lib/units";
import { pickDashboardVehicle, vehicleTitle } from "@/lib/vehicles";

import { TripPlanner, type TripVehicle } from "./_components/trip-planner";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default function TripsPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Trip Planner</h1>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Compare driving routes and see the chargers along each one.
        </p>
      </div>
      <Suspense
        fallback={
          <p role="status" className="text-sm text-neutral-500">
            Loading…
          </p>
        }
      >
        <Trips searchParams={searchParams} />
      </Suspense>
    </section>
  );
}

/**
 * The vehicle whose connectors are the default filter: the Dashboard's one
 * (primary, else the newest connected), as on the Charging page.
 */
async function loadVehicle(
  api: ApiClient,
  primaryVehicleId: string | null | undefined,
): Promise<TripVehicle | null> {
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

async function Trips({ searchParams }: { searchParams: SearchParams }) {
  await connection();
  const initial = parseTripState(await searchParams);
  const api = await requireSessionApi(tripHref(initial));

  const { data: me } = await api.GET("/api/v1/me");
  const units: UnitSystem = me?.preferences.units ?? "imperial";
  const vehicle = await loadVehicle(api, me?.preferences.primary_vehicle_id);

  return (
    <TripPlanner
      initial={initial}
      units={units}
      vehicle={vehicle}
      preferredNetworks={me?.preferences.preferred_networks ?? []}
    />
  );
}
