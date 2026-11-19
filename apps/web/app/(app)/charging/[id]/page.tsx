import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { FavoriteToggle } from "@/components/stations/favorite-toggle";
import { StationDetail } from "@/components/stations/station-detail";
import { requireSessionApi } from "@/lib/session-api";
import { pickDashboardVehicle } from "@/lib/vehicles";

import { stationPageLinks } from "../_components/charging-tab";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default function StationPage(props: Props) {
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <Suspense fallback={<p className="text-sm text-neutral-500">Loading station…</p>}>
        <Station {...props} />
      </Suspense>
    </section>
  );
}

async function Station({ params, searchParams }: Props) {
  await connection();
  const { id } = await params;
  if (!UUID_RE.test(id)) notFound();

  // The list passes its filters (and tab) along so "Back" returns to the same search.
  // They are re-parsed, never echoed raw.
  const { back, self } = stationPageLinks(id, await searchParams);

  const api = await requireSessionApi(self);
  const [{ data: me }, { data: vehicles }] = await Promise.all([
    api.GET("/api/v1/me"),
    api.GET("/api/v1/vehicles"),
  ]);
  const vehicle = vehicles
    ? pickDashboardVehicle(vehicles.items, me?.preferences.primary_vehicle_id)
    : null;

  const [{ data, response }, { data: favorites }] = await Promise.all([
    api.GET("/api/v1/stations/{station_id}", {
      params: {
        path: { station_id: id },
        query: vehicle ? { vehicle_id: vehicle.id } : {},
      },
    }),
    api.GET("/api/v1/stations/favorites"),
  ]);
  if (response.status === 404) notFound();

  return (
    <>
      <div className="flex items-start justify-between gap-3">
        <Link
          href={back}
          className="text-sm text-neutral-600 hover:underline dark:text-neutral-400"
        >
          ← Back to Charging
        </Link>
        {/* Without the favorites list we can't say whether it's starred, so no star. */}
        {data && favorites ? (
          <FavoriteToggle
            stationId={data.id}
            stationName={data.name}
            favorite={favorites.items.some((f) => f.id === data.id)}
          />
        ) : null}
      </div>
      {data ? (
        <StationDetail station={data} />
      ) : (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          Couldn&apos;t load this station. Please refresh to try again.
        </p>
      )}
    </>
  );
}
