import Link from "next/link";

import { LocalTime } from "@/components/garage/local-time";
import {
  formatDistance,
  formatPercent,
  formatPowerKw,
  type Quantity,
  type UnitSystem,
} from "@/lib/units";
import { vehicleTitle, type VehicleState, type VehicleSummary } from "@/lib/vehicles";

import { TimeAgo } from "./time-ago";

type Props = {
  vehicle: Pick<VehicleSummary, "id" | "make" | "model" | "year">;
  state: VehicleState;
  units: UnitSystem;
};

/**
 * The Dashboard for one vehicle, from `GET /api/v1/vehicles/{id}/state`.
 * Every value shows its unit and the provider timestamp it came from; missing
 * values say so instead of showing a number (PRD §7).
 */
export function VehicleStateView({ vehicle, state, units }: Props) {
  const { signals, signal_recorded_at: at } = state;
  const awaiting = !state.recorded_at;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{vehicleTitle(vehicle)}</h1>
        {state.recorded_at ? (
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Last update <LocalTime iso={state.recorded_at} />
          </p>
        ) : null}
      </div>

      {awaiting ? (
        <div
          role="status"
          data-testid="awaiting-first-update"
          className="rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-100"
        >
          <p className="font-medium">Connected — awaiting first update from your vehicle.</p>
          <p className="mt-1">
            It can take a few minutes after connecting for your car to report its battery
            state. This page will show it once it arrives.
          </p>
        </div>
      ) : (
        <>
          {state.is_stale ? (
            <div
              role="alert"
              data-testid="stale-banner"
              className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
            >
              Data may be stale — last update{" "}
              <TimeAgo iso={state.recorded_at as string} />.
            </div>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <StatCard
              title="Battery"
              quantity={formatPercent(signals.soc_percent)}
              recordedAt={at.soc_percent}
            />
            <StatCard
              title="Range"
              subtitle="Manufacturer estimate"
              quantity={formatDistance(signals.range_km, units)}
              recordedAt={at.range_km}
            />
            <StatCard
              title="Odometer"
              quantity={formatDistance(signals.odometer_km, units)}
              recordedAt={at.odometer_km}
            />
            <ChargingCard
              isCharging={signals.is_charging ?? null}
              powerKw={signals.charging_power_kw ?? null}
              recordedAt={at.is_charging}
            />
          </div>
        </>
      )}

      <QuickLinks vehicleId={vehicle.id} />
    </div>
  );
}

function Card({
  title,
  subtitle,
  recordedAt,
  children,
}: {
  title: string;
  subtitle?: string;
  recordedAt?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="space-y-2 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800"
    >
      <div>
        <h2 className="text-sm font-medium text-neutral-600 dark:text-neutral-400">{title}</h2>
        {subtitle ? (
          <p className="text-xs text-neutral-500 dark:text-neutral-500">{subtitle}</p>
        ) : null}
      </div>
      {children}
      <p className="text-xs text-neutral-500">
        {recordedAt ? (
          <>
            As of <LocalTime iso={recordedAt} />
          </>
        ) : (
          "No reading yet"
        )}
      </p>
    </section>
  );
}

function StatCard({
  title,
  subtitle,
  quantity,
  recordedAt,
}: {
  title: string;
  subtitle?: string;
  quantity: Quantity | null;
  recordedAt?: string;
}) {
  return (
    <Card title={title} subtitle={subtitle} recordedAt={quantity ? recordedAt : undefined}>
      {quantity ? (
        <p className="text-3xl font-semibold tabular-nums">
          {quantity.value}
          <span className="ml-1 text-base font-normal text-neutral-500">{quantity.unit}</span>
        </p>
      ) : (
        <p className="text-lg text-neutral-500">Not reported</p>
      )}
    </Card>
  );
}

function ChargingCard({
  isCharging,
  powerKw,
  recordedAt,
}: {
  isCharging: boolean | null;
  powerKw: number | null;
  recordedAt?: string;
}) {
  const power = formatPowerKw(powerKw);
  return (
    <Card title="Charging" recordedAt={isCharging === null ? undefined : recordedAt}>
      <p className="text-lg font-semibold">
        {isCharging === null ? (
          <span className="font-normal text-neutral-500">Not reported</span>
        ) : isCharging ? (
          "Charging"
        ) : (
          "Not charging"
        )}
      </p>
      {isCharging ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          {power ? `${power.value} ${power.unit}` : "Charging power unavailable"}
        </p>
      ) : null}
    </Card>
  );
}

function QuickLinks({ vehicleId }: { vehicleId: string }) {
  const links = [
    { href: "/trips", label: "Plan Trip" },
    { href: "/charging", label: "Find Chargers" },
    { href: `/garage/${vehicleId}`, label: "View Vehicle" },
  ];
  return (
    <nav aria-label="Quick links" className="flex flex-wrap gap-3">
      {links.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {link.label}
        </Link>
      ))}
    </nav>
  );
}

/** Shown when the user has no connected vehicle. */
export function NoVehicle() {
  return (
    <div
      data-testid="no-vehicle"
      className="space-y-3 rounded-lg border border-dashed border-neutral-300 p-6 text-center dark:border-neutral-700"
    >
      <h1 className="text-xl font-semibold">No vehicle connected</h1>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Connect your EV in the Garage to see its battery level, range and charging status here.
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
