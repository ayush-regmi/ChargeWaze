import { Plus } from "lucide-react";
import { connection } from "next/server";
import { Suspense } from "react";

import { ConnectBanner } from "@/components/garage/connect-banner";
import { EmptyGarage, VehicleCard } from "@/components/garage/vehicle-card";
import { buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { requireSessionApi } from "@/lib/session-api";

import { startVehicleConnect } from "./actions";

export default function GaragePage() {
  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <PageHeader
        title="My Garage"
        description="Connect your EV through Smartcar so Chargewise can read its battery level, range and charging status."
        actions={
          <form action={startVehicleConnect}>
            <button type="submit" className={buttonVariants({ size: "sm" })}>
              <Plus aria-hidden />
              Add vehicle
            </button>
          </form>
        }
      />
      <Suspense>
        <ConnectBanner />
      </Suspense>
      <Suspense
        fallback={<p className="text-sm text-muted-foreground">Loading vehicles…</p>}
      >
        <VehicleList />
      </Suspense>
    </section>
  );
}

async function VehicleList() {
  await connection();
  const api = await requireSessionApi("/garage");
  const [{ data }, { data: me }] = await Promise.all([
    api.GET("/api/v1/vehicles"),
    api.GET("/api/v1/me"),
  ]);
  // /me failing only hides the Primary badge; the list still renders.
  const primaryId = me?.preferences.primary_vehicle_id ?? null;

  if (!data) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Couldn&apos;t load your vehicles. Please refresh to try again.
      </p>
    );
  }
  if (data.items.length === 0) {
    return <EmptyGarage />;
  }
  return (
    <div className="space-y-3">
      {data.items.map((vehicle) => (
        <VehicleCard key={vehicle.id} vehicle={vehicle} isPrimary={vehicle.id === primaryId} />
      ))}
    </div>
  );
}
