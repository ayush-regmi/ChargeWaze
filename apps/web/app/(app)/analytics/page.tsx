import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { AnalyticsCharts } from "@/components/analytics/analytics-charts";
import { AnalyticsFilters } from "@/components/analytics/analytics-filters";
import { errorReason, rangeErrorMessage } from "@/components/analytics/errors";
import {
  analyticsSearch,
  apiQuery,
  parseAnalyticsParams,
  type AnalyticsParams,
} from "@/components/analytics/params";
import { SimulatedNotice } from "@/components/analytics/simulated-notice";
import { SummaryCards } from "@/components/analytics/summary-cards";
import { TripsTable } from "@/components/analytics/trips-table";
import { TzSync } from "@/components/analytics/tz-sync";
import { requireSessionApi } from "@/lib/session-api";
import type { UnitSystem } from "@/lib/units";
import { pickDashboardVehicle, vehicleTitle } from "@/lib/vehicles";

/** Rows per table page; the API allows up to 200. */
const TRIPS_PAGE_LIMIT = 200;

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

const LINK = "text-blue-700 hover:underline dark:text-blue-400";

export default function AnalyticsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          How much energy your drives really used, compared with what the
          planner&apos;s model expected.
        </p>
      </div>
      <Suspense fallback={null}>
        <TzSync />
      </Suspense>
      <Suspense
        fallback={
          <p className="text-sm text-neutral-500">Loading analytics…</p>
        }
      >
        <Analytics searchParams={searchParams} />
      </Suspense>
    </section>
  );
}

async function Analytics({ searchParams }: { searchParams: SearchParams }) {
  await connection();
  const raw = await searchParams;
  const params = parseAnalyticsParams(raw);
  const api = await requireSessionApi(`/analytics${analyticsSearch(params)}`);

  const [{ data: me }, { data: vehicles }] = await Promise.all([
    api.GET("/api/v1/me"),
    api.GET("/api/v1/vehicles"),
  ]);
  if (!vehicles) return <LoadError />;
  if (vehicles.items.length === 0) return <NoVehicles />;

  // A disconnected vehicle still has its history, so any of the caller's
  // vehicles can be picked; the default is the Dashboard's vehicle.
  const vehicle =
    vehicles.items.find((v) => v.id === params.vehicleId) ??
    pickDashboardVehicle(vehicles.items, me?.preferences.primary_vehicle_id) ??
    vehicles.items[0];
  const units: UnitSystem = me?.preferences.units ?? "imperial";
  const query = apiQuery(params, vehicle.id);

  const [consumption, trips] = await Promise.all([
    api.GET("/api/v1/analytics/consumption", {
      params: { query: { ...query, group_by: params.groupBy } },
    }),
    api.GET("/api/v1/analytics/trips", {
      params: {
        query: {
          ...query,
          limit: TRIPS_PAGE_LIMIT,
          ...(params.cursor ? { cursor: params.cursor } : {}),
        },
      },
    }),
  ]);

  const reset = `/analytics${analyticsSearch({ vehicleId: vehicle.id })}`;
  if (!consumption.data) {
    const message =
      consumption.response.status === 422
        ? rangeErrorMessage(errorReason(consumption.error))
        : null;
    return message ? (
      <p
        role="alert"
        className="text-sm text-neutral-700 dark:text-neutral-300"
      >
        {message}{" "}
        <Link href={reset} className={LINK}>
          Show the last 30 days
        </Link>
        .
      </p>
    ) : (
      <LoadError />
    );
  }
  if (!trips.data) {
    if (trips.response.status === 422 && params.cursor) {
      return (
        <p
          role="alert"
          className="text-sm text-neutral-700 dark:text-neutral-300"
        >
          That page link has expired.{" "}
          <Link
            href={`/analytics${analyticsSearch({ ...params, cursor: null })}`}
            className={LINK}
          >
            Go to the newest drives
          </Link>
          .
        </p>
      );
    }
    return <LoadError />;
  }

  const data = consumption.data;
  // Pin the range the API resolved, so the CSV and paging links match this view.
  const view: AnalyticsParams = {
    ...params,
    vehicleId: vehicle.id,
    from: data.range.from,
    to: data.range.to,
    cursor: null,
  };
  const csvHref = `/analytics/trips.csv${analyticsSearch({ ...view, groupBy: "week" })}`;
  const nextCursor = trips.data.next_cursor;
  const items = trips.data.items;
  const exportError =
    typeof raw.export_error === "string" ? raw.export_error : null;
  const empty = data.totals.trips === 0 && items.length === 0 && !params.cursor;

  return (
    <div className="space-y-6">
      <AnalyticsFilters
        vehicles={vehicles.items.map((v) => ({
          id: v.id,
          label: `${vehicleTitle(v)}${v.connection_status === "connected" ? "" : " (disconnected)"}`,
        }))}
        vehicleId={vehicle.id}
        from={data.range.from}
        to={data.range.to}
        groupBy={data.group_by}
        includeSimulated={data.includes_simulated}
        tz={params.tz}
      />
      <SimulatedNotice
        includesSimulated={data.includes_simulated}
        excludedSimulatedTrips={data.excluded_simulated_trips}
        includeHref={`/analytics${analyticsSearch({ ...view, includeSimulated: true })}`}
        excludeHref={`/analytics${analyticsSearch({ ...view, includeSimulated: false })}`}
      />
      {exportError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {exportError === "too_large"
            ? "Too many drives to export at once (the limit is 10,000). Pick a shorter range and try again."
            : "Couldn't export your drives. Please try again."}
        </p>
      ) : null}
      {empty ? (
        <EmptyState />
      ) : (
        <>
          <SummaryCards totals={data.totals} plans={data.plans} units={units} />
          <AnalyticsCharts
            series={data.series}
            trips={items}
            groupBy={data.group_by}
            units={units}
            tz={data.tz}
            partialTrips={Boolean(nextCursor || params.cursor)}
          />
          <section className="space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <h2 className="text-lg font-semibold">Drives</h2>
                <p className="text-xs text-neutral-500 dark:text-neutral-400">
                  Newest first. Drives marked “Not in totals” are still in
                  progress, need review, or are simulated.
                </p>
              </div>
              <a href={csvHref} className={`text-sm font-medium ${LINK}`}>
                Download CSV
              </a>
            </div>
            {items.length === 0 ? (
              <p className="text-sm text-neutral-600 dark:text-neutral-400">
                No more drives in this range.
              </p>
            ) : (
              <TripsTable trips={items} units={units} />
            )}
            {nextCursor || params.cursor ? (
              <nav aria-label="Drive pages" className="flex gap-4 text-sm">
                {params.cursor ? (
                  <Link
                    href={`/analytics${analyticsSearch(view)}`}
                    className={LINK}
                  >
                    ← Newest drives
                  </Link>
                ) : null}
                {nextCursor ? (
                  <Link
                    href={`/analytics${analyticsSearch({ ...view, cursor: nextCursor })}`}
                    className={LINK}
                  >
                    Older drives →
                  </Link>
                ) : null}
              </nav>
            ) : null}
          </section>
        </>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div
      data-testid="analytics-empty"
      className="rounded-lg border border-dashed border-neutral-300 p-8 text-center dark:border-neutral-700"
    >
      <p className="font-medium">
        No trips yet. Trips appear here automatically after you drive.
      </p>
      <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
        Try a longer date range if you expected to see drives here.
      </p>
    </div>
  );
}

function NoVehicles() {
  return (
    <div className="space-y-3 rounded-lg border border-dashed border-neutral-300 p-6 text-center dark:border-neutral-700">
      <p className="font-medium">No vehicle connected</p>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Connect your EV in the Garage. Its drives show up here automatically.
      </p>
      <Link
        href="/garage"
        className="inline-block rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
      >
        Go to Garage
      </Link>
    </div>
  );
}

function LoadError() {
  return (
    <p role="alert" className="text-sm text-red-600 dark:text-red-400">
      Couldn&apos;t load your analytics. Please refresh to try again.
    </p>
  );
}
