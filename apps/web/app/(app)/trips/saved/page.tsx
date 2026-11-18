import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { TripList } from "@/components/trips/trip-list";
import { requireSessionApi } from "@/lib/session-api";
import type { UnitSystem } from "@/lib/units";

const SAVED_TRIPS_PAGE_LIMIT = 20;

// The API's cursor is opaque base64url (api-contracts §3.8, max 512 chars).
// Anything else is dropped instead of being echoed back to the API.
const CURSOR_RE = /^[A-Za-z0-9_=-]{1,512}$/;

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default function SavedTripsPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Saved trips</h1>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Plans you saved, newest first. Each one shows the plan as it was made.
        </p>
      </div>
      <Suspense fallback={<p className="text-sm text-neutral-500">Loading saved trips…</p>}>
        <SavedTrips searchParams={searchParams} />
      </Suspense>
    </section>
  );
}

async function SavedTrips({ searchParams }: { searchParams: SearchParams }) {
  await connection();
  const raw = (await searchParams).cursor;
  const cursor = typeof raw === "string" && CURSOR_RE.test(raw) ? raw : null;
  const api = await requireSessionApi(
    cursor ? `/trips/saved?cursor=${encodeURIComponent(cursor)}` : "/trips/saved",
  );

  const [{ data: me }, list] = await Promise.all([
    api.GET("/api/v1/me"),
    api
      .GET("/api/v1/trips", {
        params: {
          query: { saved: true, limit: SAVED_TRIPS_PAGE_LIMIT, ...(cursor ? { cursor } : {}) },
        },
      })
      .catch(() => null),
  ]);
  const units: UnitSystem = me?.preferences.units ?? "imperial";

  if (list?.response.status === 422 && cursor) {
    return (
      <p role="alert" className="text-sm text-neutral-700 dark:text-neutral-300">
        That page link has expired.{" "}
        <Link href="/trips/saved" className="text-blue-700 hover:underline dark:text-blue-400">
          Go to the first page
        </Link>
        .
      </p>
    );
  }
  if (!list?.data) {
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        Couldn&apos;t load your saved trips. Please refresh to try again.
      </p>
    );
  }

  const next = list.data.next_cursor;
  return (
    <TripList
      items={list.data.items}
      units={units}
      nextHref={next ? `/trips/saved?cursor=${encodeURIComponent(next)}` : null}
      firstHref={cursor ? "/trips/saved" : null}
    />
  );
}
