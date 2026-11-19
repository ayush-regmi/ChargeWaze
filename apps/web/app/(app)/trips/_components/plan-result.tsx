"use client";

import Link from "next/link";
import { useState, useTransition } from "react";

import { LocalTime } from "@/components/garage/local-time";
import { formatMinutes, formatSoc, INFEASIBLE_TITLES } from "@/components/trips/plan-payload";
import { Button } from "@/components/ui/button";
import { formatDistanceText } from "@/lib/geo-format";
import { stopSocLabel, type PlanResponse, type PlanStop } from "@/lib/plan-format";
import type { UnitSystem } from "@/lib/units";

import { saveTrip, type SaveTripResult } from "./plan-actions";
import { PlanAssumptions } from "./plan-assumptions";

function StartingCharge({ plan }: { plan: PlanResponse }) {
  return (
    <p className="text-sm text-neutral-600 dark:text-neutral-400" data-testid="plan-starting-soc">
      Starting charge {formatSoc(plan.starting_soc)}
      {plan.starting_soc_source === "override" ? (
        " (entered by you)"
      ) : plan.starting_soc_recorded_at ? (
        <>
          {" "}
          (from your car, <LocalTime iso={plan.starting_soc_recorded_at} />)
        </>
      ) : (
        " (from your car)"
      )}
    </p>
  );
}

function InfeasibleNotice({ plan, units }: { plan: PlanResponse; units: UnitSystem }) {
  const reason = plan.reason;
  const seg = reason?.limiting_segment ?? null;
  return (
    <div
      role="alert"
      data-testid="plan-infeasible"
      className="space-y-1.5 rounded-lg border-2 border-red-400 bg-red-50 p-4 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/50 dark:text-red-200"
    >
      <p className="text-base font-semibold">
        Not feasible{reason ? `: ${INFEASIBLE_TITLES[reason.code] ?? reason.code}` : ""}
      </p>
      <p>{reason?.message ?? "The planner didn't give a reason."}</p>
      {seg ? (
        <p>
          Problem stretch (red on the map): from {formatDistanceText(seg.from_km, units)} to{" "}
          {formatDistanceText(seg.to_km, units)} along the route, which needs{" "}
          {formatSoc(seg.required_soc)} charge with {formatSoc(seg.available_soc)} available.
        </p>
      ) : null}
      <p className="text-xs">
        This route isn&apos;t a valid plan for your battery. Try another route, a lower reserve
        or a higher charge-to limit below.
      </p>
    </div>
  );
}

function StopItem({ stop, units }: { stop: PlanStop; units: UnitSystem }) {
  const details = [stop.station.network, stop.station.connector, `${stop.station.power_kw} kW`]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="space-y-0.5 p-3" data-testid="plan-stop">
      <p className="font-medium">
        <span className="mr-2 inline-flex h-6 w-6 items-center justify-center rounded-full bg-blue-600 text-xs text-white">
          {stop.sequence}
        </span>
        <Link
          href={`/charging/${stop.station.id}`}
          className="text-blue-700 hover:underline dark:text-blue-400"
        >
          {stop.station.name}
        </Link>
      </p>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">{details}</p>
      <p className="text-sm">
        <span data-testid="plan-stop-soc">{stopSocLabel(stop)}</span> · charge{" "}
        {formatMinutes(stop.charging_minutes)}
      </p>
      <p className="text-xs text-neutral-500">
        {formatDistanceText(stop.distance_along_route_km, units)} along the route · detour{" "}
        {formatMinutes(stop.detour_minutes)} (estimate)
      </p>
    </li>
  );
}

function Total({ label, value, strong }: { label: string; value: string | null; strong?: boolean }) {
  return (
    <div className="flex justify-between gap-3 py-0.5 text-sm">
      <dt className="text-neutral-600 dark:text-neutral-400">{label}</dt>
      <dd className={strong ? "font-semibold" : undefined}>{value ?? "—"}</dd>
    </div>
  );
}

const SAVE_ERRORS: Record<Exclude<SaveTripResult, { ok: true }>["error"], string> = {
  NOT_FOUND: "This plan no longer exists. Plan again to save it.",
  UNAUTHENTICATED: "Your session has ended. Sign in again to save.",
  UNKNOWN: "Couldn't save this trip. Try again.",
};

function SaveTrip({ tripId }: { tripId: string }) {
  const [state, setState] = useState<SaveTripResult | null>(null);
  const [pending, startTransition] = useTransition();
  if (state?.ok) {
    return (
      <p role="status" className="text-sm text-green-700 dark:text-green-400">
        Saved.{" "}
        <Link href={`/trips/${tripId}`} className="font-medium underline-offset-2 hover:underline">
          View trip
        </Link>
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        size="sm"
        disabled={pending}
        onClick={() => startTransition(async () => setState(await saveTrip(tripId)))}
      >
        {pending ? "Saving…" : "Save trip"}
      </Button>
      {state && !state.ok ? (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {SAVE_ERRORS[state.error]}
        </p>
      ) : null}
    </div>
  );
}

/**
 * One `POST /routes/plan` result (api §3.7): the stops with arrival →
 * departure charge, charging and detour time and totals, or a prominent
 * "not feasible" with the reason. Every number is the API's; nothing is
 * recomputed here.
 */
export function PlanResult({ plan, units }: { plan: PlanResponse; units: UnitSystem }) {
  const totals = plan.totals;
  return (
    <div className="space-y-3" data-testid="plan-result" data-status={plan.status}>
      {plan.status === "infeasible" || !totals ? (
        <InfeasibleNotice plan={plan} units={units} />
      ) : (
        <>
          <div className="space-y-1 rounded-lg border border-green-300 bg-green-50 p-3 dark:border-green-900 dark:bg-green-950/40">
            <p className="font-semibold text-green-900 dark:text-green-200">
              {plan.stops.length === 0
                ? "No charging needed"
                : `${plan.stops.length} charging ${plan.stops.length === 1 ? "stop" : "stops"}`}
              {" · "}arrive with {formatSoc(plan.arrival_soc)}
            </p>
            <dl data-testid="plan-totals">
              <Total label="Distance" value={formatDistanceText(plan.route?.distance_km, units)} />
              <Total label="Driving" value={formatMinutes(totals.drive_minutes)} />
              <Total label="Charging" value={formatMinutes(totals.charging_minutes)} />
              <Total label="Detours (estimate)" value={formatMinutes(totals.detour_minutes)} />
              <Total label="Stop overhead" value={formatMinutes(totals.stop_overhead_minutes)} />
              <Total label="Total time" value={formatMinutes(totals.total_minutes)} strong />
            </dl>
          </div>
          {plan.stops.length > 0 ? (
            <ol
              aria-label="Charging stops"
              className="divide-y divide-neutral-200 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800"
            >
              {plan.stops.map((s) => (
                <StopItem key={s.sequence} stop={s} units={units} />
              ))}
            </ol>
          ) : null}
        </>
      )}
      <StartingCharge plan={plan} />
      <PlanAssumptions plan={plan} units={units} />
      <SaveTrip key={plan.trip_id} tripId={plan.trip_id} />
    </div>
  );
}
