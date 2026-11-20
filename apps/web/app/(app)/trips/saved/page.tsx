import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { ObservationList, type ObservationFilter } from "@/components/trips/observation-list";
import { TripList } from "@/components/trips/trip-list";
import { requireSessionApi } from "@/lib/session-api";
import type { UnitSystem } from "@/lib/units";

const SAVED_TRIPS_PAGE_LIMIT = 20;
const OBSERVED_PAGE_LIMIT = 20;

// The API's cursor is opaque base64url (api-contracts §3.8, max 512 chars).
// Anything else is dropped instead of being echoed back to the API.
const CURSOR_RE = /^[A-Za-z0-9_=-]{1,512}$/;

type RawParams = Record<string, string | string[] | undefined>;
type SearchParams = Promise<RawParams>;
type View = "saved" | "observed";

function one(params: RawParams, key: string): string | null {
  const v = params[key];
  return typeof v === "string" ? v : null;
}

/** `/trips/saved` URLs for the observed tab (P5-F6 links to the needs-review one). */
function observedHref(filter: ObservationFilter, cursor: string | null = null): string {
  const q = new URLSearchParams({ view: "observed" });
  if (filter === "needs_review") q.set("review", "needs_review");
  if (cursor) q.set("cursor", cursor);
  return `/trips/saved?${q.toString()}`;
}

function Tabs({ view }: { view: View }) {
  const tabs: { key: View; label: string; href: string }[] = [
    { key: "saved", label: "Saved plans", href: "/trips/saved" },
    { key: "observed", label: "Observed drives", href: observedHref("all") },
  ];
  return (
    <nav aria-label="Trip history" className="flex gap-4 border-b border-border text-sm">
      {tabs.map((t) => (
        <Link
          key={t.key}
          href={t.href}
          aria-current={view === t.key ? "page" : undefined}
          className={
            view === t.key
              ? "-mb-px border-b-2 border-primary pb-2 font-medium"
              : "pb-2 text-muted-foreground hover:text-foreground"
          }
        >
          {t.label}
        </Link>
      ))}
    </nav>
  );
}

export default function SavedTripsPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading trips…</p>}>
        <TripHistory searchParams={searchParams} />
      </Suspense>
    </section>
  );
}

async function TripHistory({ searchParams }: { searchParams: SearchParams }) {
  await connection();
  const params = await searchParams;
  const view: View = one(params, "view") === "observed" ? "observed" : "saved";
  const raw = one(params, "cursor");
  const cursor = raw && CURSOR_RE.test(raw) ? raw : null;

  return (
    <>
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          {view === "saved" ? "Saved trips" : "Observed drives"}
        </h1>
        <p className="text-sm text-muted-foreground">
          {view === "saved"
            ? "Plans you saved, newest first. Each one shows the plan as it was made."
            : "Drives recorded from your car, newest first. Check the ones marked for review."}
        </p>
      </div>
      <Tabs view={view} />
      {view === "saved" ? (
        <SavedTrips cursor={cursor} />
      ) : (
        <ObservedDrives
          cursor={cursor}
          filter={one(params, "review") === "needs_review" ? "needs_review" : "all"}
        />
      )}
    </>
  );
}

function ExpiredPage({ href }: { href: string }) {
  return (
    <p role="alert" className="text-sm text-foreground">
      That page link has expired.{" "}
      <Link href={href} className="link">
        Go to the first page
      </Link>
      .
    </p>
  );
}

async function SavedTrips({ cursor }: { cursor: string | null }) {
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

  if (list?.response.status === 422 && cursor) return <ExpiredPage href="/trips/saved" />;
  if (!list?.data) {
    return (
      <p role="alert" className="text-sm text-destructive">
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

async function ObservedDrives({
  cursor,
  filter,
}: {
  cursor: string | null;
  filter: ObservationFilter;
}) {
  const api = await requireSessionApi(observedHref(filter, cursor));
  const [{ data: me }, list] = await Promise.all([
    api.GET("/api/v1/me"),
    api
      .GET("/api/v1/observed-trips", {
        params: {
          query: {
            limit: OBSERVED_PAGE_LIMIT,
            ...(filter === "needs_review" ? { review_status: ["needs_review" as const] } : {}),
            ...(cursor ? { cursor } : {}),
          },
        },
      })
      .catch(() => null),
  ]);
  const units: UnitSystem = me?.preferences.units ?? "imperial";

  if (list?.response.status === 422 && cursor) return <ExpiredPage href={observedHref(filter)} />;
  if (!list?.data) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Couldn&apos;t load your drives. Please refresh to try again.
      </p>
    );
  }

  const next = list.data.next_cursor;
  return (
    <ObservationList
      items={list.data.items}
      units={units}
      filter={filter}
      filterHrefs={{ all: observedHref("all"), needs_review: observedHref("needs_review") }}
      nextHref={next ? observedHref(filter, next) : null}
      firstHref={cursor ? observedHref(filter) : null}
    />
  );
}
