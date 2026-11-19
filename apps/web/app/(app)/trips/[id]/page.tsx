import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { TripDetail } from "@/components/trips/trip-detail";
import { requireSessionApi } from "@/lib/session-api";
import type { UnitSystem } from "@/lib/units";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Props = { params: Promise<{ id: string }> };

export default function TripPage(props: Props) {
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <Suspense fallback={<p className="text-sm text-neutral-500">Loading trip…</p>}>
        <Trip {...props} />
      </Suspense>
    </section>
  );
}

async function Trip({ params }: Props) {
  await connection();
  const { id } = await params;
  if (!UUID_RE.test(id)) notFound();

  const api = await requireSessionApi(`/trips/${id}`);
  const [{ data: me }, trip] = await Promise.all([
    api.GET("/api/v1/me"),
    api
      .GET("/api/v1/trips/{trip_id}", { params: { path: { trip_id: id } } })
      .catch(() => null),
  ]);
  if (trip?.response.status === 404) notFound();
  const units: UnitSystem = me?.preferences.units ?? "imperial";

  return (
    <>
      <Link
        href="/trips/saved"
        className="text-sm text-neutral-600 hover:underline dark:text-neutral-400"
      >
        ← Saved trips
      </Link>
      {trip?.data ? (
        <TripDetail trip={trip.data} units={units} />
      ) : (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          Couldn&apos;t load this trip. Please refresh to try again.
        </p>
      )}
    </>
  );
}
