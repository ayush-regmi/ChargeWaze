"use client";

import Link from "next/link";
import { useState, useTransition } from "react";

import type { Preferences } from "@/components/profile/preferences";
import { Button } from "@/components/ui/button";
import {
  specFieldLabel,
  type PlanErrorCode,
  type PlanOutcome,
  type PlanPreferences,
} from "@/lib/plan-format";
import type { TripPoint } from "@/lib/trip-url";
import type { UnitSystem } from "@/lib/units";

import { planTrip } from "./plan-actions";
import { PlanPreferencesPanel } from "./plan-preferences";
import { PlanResult } from "./plan-result";
import { PlanSocPrompt } from "./plan-soc-prompt";

const ERROR_TEXT: Record<PlanErrorCode, string> = {
  MAPBOX_NOT_CONFIGURED: "Route planning isn't set up on this server yet.",
  MAPBOX_UNREACHABLE: "The map provider isn't responding right now.",
  MAPBOX_ERROR: "The map provider couldn't plan this route.",
  STATIONS_NOT_CONFIGURED: "Charger search isn't set up on this server yet.",
  STATIONS_UNREACHABLE: "The charger directory isn't responding right now.",
  NOT_FOUND: "This vehicle is no longer in your Garage.",
  VALIDATION_ERROR: "These places can't be planned. Pick two different places.",
  UNAUTHENTICATED: "Your session has ended. Sign in again to plan a trip.",
  UNKNOWN: "Couldn't plan this trip.",
};

type Request = { preferences: PlanPreferences | null; startingSoc: number | null };

function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) {
  return (
    <div
      role="alert"
      className={
        tone === "error"
          ? "space-y-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
          : "space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
      }
    >
      {children}
    </div>
  );
}

/**
 * Charging plan for the selected route (phase4_plan F6): one button that
 * calls `POST /routes/plan`, then the result, the stale-battery prompt, a
 * "vehicle specs incomplete" notice, or an error. The parent keeps each
 * route's outcome (so the map can show the stops) and remounts this panel
 * per route, so one route's plan is never shown for another.
 */
export function PlanPanel({
  vehicleId,
  vehicleTitle,
  origin,
  destination,
  routeIndex,
  routeName,
  preferences,
  units,
  outcome,
  onOutcome,
  signInHref,
}: {
  vehicleId: string | null;
  vehicleTitle: string | null;
  origin: TripPoint;
  destination: TripPoint;
  routeIndex: number;
  routeName: string;
  preferences: Preferences | null;
  units: UnitSystem;
  outcome: PlanOutcome | null;
  onOutcome: (outcome: PlanOutcome) => void;
  signInHref: string;
}) {
  const [pending, startTransition] = useTransition();
  const [last, setLast] = useState<Request>({ preferences: null, startingSoc: null });

  const run = (request: Request) => {
    if (!vehicleId) return;
    setLast(request);
    startTransition(async () => {
      const result = await planTrip({
        vehicleId,
        origin,
        destination,
        routeIndex,
        preferences: request.preferences,
        startingSocOverride: request.startingSoc,
      });
      onOutcome(result);
    });
  };

  return (
    <section aria-labelledby="plan-title" className="space-y-3" data-testid="plan-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="plan-title" className="text-base font-semibold">
          Charging plan for {routeName}
        </h2>
        {vehicleId ? (
          <Button
            type="button"
            size="sm"
            variant={outcome ? "outline" : "default"}
            disabled={pending}
            onClick={() => run(last)}
          >
            {outcome ? "Plan again" : "Plan charging stops"}
          </Button>
        ) : null}
      </div>

      {!vehicleId ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          <Link href="/garage" className="font-medium underline-offset-2 hover:underline">
            Connect a vehicle
          </Link>{" "}
          to plan charging stops for its battery.
        </p>
      ) : pending ? (
        <p role="status" className="text-sm text-neutral-500">
          Planning charging stops on {routeName}…
        </p>
      ) : !outcome ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Find where to charge your {vehicleTitle ?? "vehicle"} on {routeName}, how long each
          stop takes and the total trip time.
        </p>
      ) : outcome.kind === "plan" ? (
        <PlanResult plan={outcome.plan} units={units} />
      ) : outcome.kind === "soc_required" ? (
        <PlanSocPrompt
          lastSoc={outcome.lastSoc}
          lastRecordedAt={outcome.lastRecordedAt}
          pending={pending}
          onSubmit={(soc) => run({ ...last, startingSoc: soc })}
        />
      ) : outcome.kind === "specs_incomplete" ? (
        <Notice tone="info">
          <p>
            Your {vehicleTitle ?? "vehicle"} is missing{" "}
            {outcome.missing.length > 0
              ? outcome.missing.map(specFieldLabel).join(", ")
              : "some specs"}
            , so its battery can&apos;t be planned yet.
          </p>
          <Link
            href={`/garage/${vehicleId}`}
            className="font-medium underline-offset-2 hover:underline"
          >
            Add them in the Garage
          </Link>
        </Notice>
      ) : outcome.kind === "route_unavailable" ? (
        <Notice tone="error">
          <p>The map provider no longer offers {routeName}. Search the trip again.</p>
        </Notice>
      ) : outcome.kind === "preferences_invalid" ? (
        <Notice tone="error">
          <p>
            The reserve must be below the charge-to limit
            {outcome.maxChargeSoc !== null
              ? ` and below ${Math.round(outcome.maxChargeSoc * 100)}%, the most this vehicle charges to`
              : ""}
            . Adjust the preferences below.
          </p>
        </Notice>
      ) : (
        <Notice tone="error">
          <p>{ERROR_TEXT[outcome.code]}</p>
          {outcome.code === "UNAUTHENTICATED" ? (
            <Link href={signInHref} className="font-medium underline-offset-2 hover:underline">
              Sign in
            </Link>
          ) : (
            <Button type="button" size="sm" variant="outline" onClick={() => run(last)}>
              Try again
            </Button>
          )}
        </Notice>
      )}

      {/* Hidden while the battery prompt is open: it has its own charge field. */}
      {vehicleId && preferences && outcome && outcome.kind !== "soc_required" ? (
        <PlanPreferencesPanel
          stored={preferences}
          pending={pending}
          onReplan={(r) =>
            run({ preferences: r.preferences, startingSoc: r.startingSoc ?? last.startingSoc })
          }
        />
      ) : null}
    </section>
  );
}
