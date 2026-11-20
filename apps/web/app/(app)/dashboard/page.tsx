import { connection } from "next/server";
import { Suspense } from "react";

import { NoVehicle, VehicleStateView } from "@/components/dashboard/vehicle-state-view";
import { requireSessionApi } from "@/lib/session-api";
import type { UnitSystem } from "@/lib/units";
import { pickDashboardVehicle } from "@/lib/vehicles";

export default function DashboardPage() {
  return (
    <section className="mx-auto max-w-5xl space-y-6">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading dashboard…</p>}>
        <Dashboard />
      </Suspense>
    </section>
  );
}

async function Dashboard() {
  await connection();
  const api = await requireSessionApi("/dashboard");
  const [{ data: me }, { data: vehicles }] = await Promise.all([
    api.GET("/api/v1/me"),
    api.GET("/api/v1/vehicles"),
  ]);

  if (!vehicles) return <LoadError />;
  const vehicle = pickDashboardVehicle(vehicles.items, me?.preferences.primary_vehicle_id);
  if (!vehicle) return <NoVehicle />;

  // Served from the backend's cached latest_state, never a live Smartcar call.
  const { data: state } = await api.GET("/api/v1/vehicles/{vehicle_id}/state", {
    params: { path: { vehicle_id: vehicle.id } },
  });
  if (!state) return <LoadError />;

  // /me failing shouldn't hide the car; fall back to the default preference.
  const units: UnitSystem = me?.preferences.units ?? "imperial";
  return <VehicleStateView vehicle={vehicle} state={state} units={units} />;
}

function LoadError() {
  return (
    <p role="alert" className="text-sm text-destructive">
      Couldn&apos;t load your vehicle. Please refresh to try again.
    </p>
  );
}
