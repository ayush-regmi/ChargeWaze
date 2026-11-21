import Link from "next/link";
import type { ReactNode } from "react";

import type { components } from "@chargewaze/api-client";

import { LocalTime } from "@/components/garage/local-time";
import { DataAttribution } from "@/components/map/attribution";
import { formatDistanceText } from "@/lib/geo-format";
import type { UnitSystem } from "@/lib/units";

import { DeleteTripButton } from "./delete-trip-button";
import {
  formatMinutes,
  formatSoc,
  INFEASIBLE_TITLES,
  parseStoredPlan,
  type PlanStop,
  type StoredPlan,
} from "./plan-payload";
import { PlanStatusBadge, tripTitle } from "./trip-list";
import { TripMap } from "./trip-map";

export type TripDetailData = components["schemas"]["TripDetail"];

const MISSING = "—";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[11rem_1fr] gap-3 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  vehicle: "your vehicle specs",
  catalog: "the vehicle catalog",
  catalog_derived: "derived from published figures, not measured",
  telemetry: "vehicle telemetry",
  override: "entered by you",
};

function source(s: string | null): string {
  return s ? (SOURCE_LABELS[s] ?? s) : MISSING;
}

function InfeasibleNotice({ plan, units }: { plan: StoredPlan; units: UnitSystem }) {
  const code = plan.reason?.code ?? null;
  const seg = plan.reason?.limiting_segment ?? null;
  const from = formatDistanceText(seg?.from_km, units);
  const to = formatDistanceText(seg?.to_km, units);
  return (
    <div
      role="alert"
      className="space-y-1 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive"
    >
      <p className="font-semibold">
        This trip can&apos;t be driven as planned
        {code ? `: ${INFEASIBLE_TITLES[code] ?? code}` : ""}
      </p>
      <p>{plan.reason?.message ?? "The plan didn't record a reason."}</p>
      {from && to ? (
        <p>
          Problem stretch: from {from} to {to} along the route
          {seg?.required_soc !== null && seg?.available_soc !== null
            ? ` (needs ${formatSoc(seg?.required_soc)} charge, ${formatSoc(seg?.available_soc)} available)`
            : ""}
          .
        </p>
      ) : null}
    </div>
  );
}

function StopItem({ stop, units }: { stop: PlanStop; units: UnitSystem }) {
  const name = stop.station.name ?? "Unnamed station";
  const arrival = formatSoc(stop.arrival_soc) ?? MISSING;
  const departure = formatSoc(stop.departure_soc) ?? MISSING;
  const details = [
    stop.station.network,
    stop.station.connector,
    stop.station.power_kw !== null ? `${stop.station.power_kw} kW` : null,
  ].filter(Boolean);
  return (
    <li className="space-y-1 p-4">
      <p className="font-medium">
        <span className="mr-2 inline-flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
          {stop.sequence}
        </span>
        {stop.station.id ? (
          <Link
            href={`/charging/${stop.station.id}`}
            className="link"
          >
            {name}
          </Link>
        ) : (
          name
        )}
      </p>
      {details.length > 0 ? (
        <p className="text-sm text-muted-foreground">{details.join(" · ")}</p>
      ) : null}
      <p className="text-sm">
        Arrive {arrival} → leave {departure} · charge{" "}
        {formatMinutes(stop.charging_minutes) ?? MISSING}
      </p>
      <p className="text-xs text-muted-foreground">
        At {formatDistanceText(stop.distance_along_route_km, units) ?? MISSING} along the route ·
        detour {formatMinutes(stop.detour_minutes) ?? MISSING} (estimate)
      </p>
    </li>
  );
}

/**
 * One saved trip, rendered from the stored `plan_payload` exactly as it was
 * planned (phase4_plan F7, api-contracts §3.9). Nothing is recomputed; values
 * missing from the stored plan read "—".
 */
export function TripDetail({ trip, units }: { trip: TripDetailData; units: UnitSystem }) {
  const plan = parseStoredPlan(trip.plan);
  const title = tripTitle(plan?.origin?.label ?? null, plan?.destination?.label ?? null);
  const infeasible = trip.plan_status === "infeasible";

  return (
    <article aria-labelledby="trip-title" className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 id="trip-title" className="text-2xl font-semibold tracking-tight">
            {title}
          </h1>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <PlanStatusBadge planStatus={trip.plan_status} />
            <span>
              Planned <LocalTime iso={trip.created_at} />
            </span>
            {plan?.route?.summary ? <span>· via {plan.route.summary}</span> : null}
          </div>
        </div>
        <DeleteTripButton tripId={trip.id} tripName={title} />
      </header>

      {!plan ? (
        <p role="alert" className="text-sm text-destructive">
          This trip&apos;s stored plan couldn&apos;t be read.
        </p>
      ) : (
        <>
          {infeasible ? <InfeasibleNotice plan={plan} units={units} /> : null}

          {plan.route?.geometry_polyline6 ? (
            <div className="space-y-1">
              <TripMap plan={plan} label={`Route map: ${title}`} />
              <DataAttribution />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No route map: this plan has no route.</p>
          )}

          <section aria-labelledby="summary-title" className="space-y-2">
            <h2 id="summary-title" className="text-sm font-medium">
              Summary
            </h2>
            <dl className="divide-y divide-border">
              <Row label="Distance">
                {formatDistanceText(plan.route?.distance_km, units) ?? MISSING}
              </Row>
              <Row label="Starting charge">
                {formatSoc(plan.starting_soc) ?? MISSING}
                {plan.starting_soc_source ? ` (${source(plan.starting_soc_source)})` : ""}
              </Row>
              {plan.totals ? (
                <>
                  <Row label="Arrival charge">{formatSoc(plan.arrival_soc) ?? MISSING}</Row>
                  <Row label="Driving">{formatMinutes(plan.totals.drive_minutes) ?? MISSING}</Row>
                  <Row label="Charging">
                    {formatMinutes(plan.totals.charging_minutes) ?? MISSING}
                  </Row>
                  <Row label="Detours (estimate)">
                    {formatMinutes(plan.totals.detour_minutes) ?? MISSING}
                  </Row>
                  <Row label="Stop overhead">
                    {formatMinutes(plan.totals.stop_overhead_minutes) ?? MISSING}
                  </Row>
                  <Row label="Total time">
                    <span className="font-medium">
                      {formatMinutes(plan.totals.total_minutes) ?? MISSING}
                    </span>
                  </Row>
                </>
              ) : (
                <Row label="Driving">{formatMinutes(plan.route?.drive_minutes) ?? MISSING}</Row>
              )}
            </dl>
          </section>

          {!infeasible ? (
            <section aria-labelledby="stops-title" className="space-y-2">
              <h2 id="stops-title" className="text-sm font-medium">
                Charging stops
              </h2>
              {plan.stops.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No charging needed on this trip.
                </p>
              ) : (
                <ol className="divide-y divide-border rounded-lg border border-border">
                  {plan.stops.map((s) => (
                    <StopItem key={s.sequence} stop={s} units={units} />
                  ))}
                </ol>
              )}
            </section>
          ) : null}

          {plan.assumptions ? (
            <details className="rounded-lg border border-border p-4 text-sm">
              <summary className="cursor-pointer font-medium">How this was calculated</summary>
              <dl className="mt-2">
                <Row label="Usable battery">
                  {plan.assumptions.usable_capacity_kwh !== null
                    ? `${plan.assumptions.usable_capacity_kwh} kWh`
                    : MISSING}{" "}
                  ({source(plan.assumptions.usable_capacity_source)})
                </Row>
                <Row label="Consumption">
                  {plan.assumptions.baseline_kwh_per_km !== null
                    ? `${plan.assumptions.baseline_kwh_per_km} kWh/km`
                    : MISSING}{" "}
                  ({source(plan.assumptions.baseline_source)})
                </Row>
                <Row label="Charging curve">{source(plan.assumptions.charging_curve_source)}</Row>
                <Row label="Reserve">{formatSoc(plan.assumptions.reserve_soc) ?? MISSING}</Row>
                <Row label="Charge to">{formatSoc(plan.assumptions.charge_to_soc) ?? MISSING}</Row>
                <Row label="Max detour">
                  {formatMinutes(plan.assumptions.max_detour_minutes) ?? MISSING}
                </Row>
                <Row label="Per-stop overhead">
                  {formatMinutes(plan.assumptions.stop_overhead_minutes) ?? MISSING}
                </Row>
                <Row label="Preferred networks">
                  {plan.assumptions.preferred_networks.length > 0
                    ? plan.assumptions.preferred_networks.join(", ")
                    : "None"}
                  {plan.assumptions.preferred_networks_relaxed
                    ? " (no plan used only these, so all networks were allowed)"
                    : ""}
                </Row>
                {plan.candidates?.searched ? (
                  <Row label="Stations">
                    {plan.candidates.considered ?? MISSING} considered;{" "}
                    {plan.candidates.excluded_incompatible ?? 0} incompatible,{" "}
                    {plan.candidates.excluded_unknown_power ?? 0} with unknown power,{" "}
                    {plan.candidates.excluded_detour ?? 0} too far off route excluded
                  </Row>
                ) : null}
              </dl>
              <p className="mt-2 text-xs text-muted-foreground">
                Speed, temperature and elevation aren&apos;t modelled. Detours are estimates.
                This is the plan as it was saved; it isn&apos;t recalculated.
              </p>
            </details>
          ) : null}
        </>
      )}
    </article>
  );
}
