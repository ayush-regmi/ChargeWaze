import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { DisconnectButton } from "@/components/garage/disconnect-button";
import { FreshnessBadge } from "@/components/garage/freshness-badge";
import { LocalTime } from "@/components/garage/local-time";
import { SpecsPanel } from "@/components/garage/specs-panel";
import { SignalList } from "@/components/garage/vehicle-card";
import { requireSessionApi } from "@/lib/session-api";
import { vehicleTitle } from "@/lib/vehicles";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function VehiclePage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <Link
        href="/garage"
        className="text-sm text-neutral-600 hover:underline dark:text-neutral-400"
      >
        ← Back to Garage
      </Link>
      <Suspense fallback={<p className="text-sm text-neutral-500">Loading vehicle…</p>}>
        <VehicleDetails params={params} />
      </Suspense>
    </section>
  );
}

async function VehicleDetails({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  if (!UUID_RE.test(id)) notFound();

  const api = await requireSessionApi(`/garage/${id}`);
  const { data, response } = await api.GET("/api/v1/vehicles/{vehicle_id}", {
    params: { path: { vehicle_id: id } },
  });
  if (response.status === 404) notFound();
  if (!data) {
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        Couldn&apos;t load this vehicle. Please refresh to try again.
      </p>
    );
  }

  const title = vehicleTitle(data);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="text-sm capitalize text-neutral-600 dark:text-neutral-400">
            {data.connection_status}
            {data.connection_status === "connected" && data.connected_at ? (
              <span className="normal-case">
                {" "}
                since <LocalTime iso={data.connected_at} />
              </span>
            ) : null}
            {data.connection_status === "disconnected" && data.disconnected_at ? (
              <span className="normal-case">
                {" "}
                on <LocalTime iso={data.disconnected_at} />
              </span>
            ) : null}
          </p>
        </div>
        {data.connection_status === "connected" ? (
          <DisconnectButton vehicleId={data.id} vehicleName={title} />
        ) : null}
      </div>

      <FreshnessBadge vehicle={data} />

      <div className="space-y-2">
        <h2 className="text-sm font-medium">Supported data</h2>
        <SignalList signals={data.supported_signals} />
      </div>

      <SpecsPanel specs={data.specs} />
    </div>
  );
}
