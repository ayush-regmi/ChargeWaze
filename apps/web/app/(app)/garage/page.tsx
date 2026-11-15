import { connection } from "next/server";
import { Suspense } from "react";

import { ConnectBanner } from "@/components/garage/connect-banner";
import { EmptyGarage, VehicleCard } from "@/components/garage/vehicle-card";
import { requireSessionApi } from "@/lib/session-api";

import { startVehicleConnect } from "./actions";

export default function GaragePage() {
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">My Garage</h1>
          <p className="text-neutral-600 dark:text-neutral-400">
            Connect your EV through Smartcar so Chargewise can read its battery
            level, range and charging status.
          </p>
        </div>
        <form action={startVehicleConnect}>
          <button
            type="submit"
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-emerald-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
          >
            Add vehicle
          </button>
        </form>
      </div>
      <Suspense>
        <ConnectBanner />
      </Suspense>
      <Suspense
        fallback={<p className="text-sm text-neutral-500">Loading vehicles…</p>}
      >
        <VehicleList />
      </Suspense>
    </section>
  );
}

async function VehicleList() {
  await connection();
  const api = await requireSessionApi("/garage");
  const { data } = await api.GET("/api/v1/vehicles");

  if (!data) {
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        Couldn&apos;t load your vehicles. Please refresh to try again.
      </p>
    );
  }
  if (data.items.length === 0) {
    return <EmptyGarage />;
  }
  return (
    <div className="space-y-4">
      {data.items.map((vehicle) => (
        <VehicleCard key={vehicle.id} vehicle={vehicle} />
      ))}
    </div>
  );
}
