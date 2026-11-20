import { ArrowUpRight, CarFront } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { cn } from "@/lib/utils";

import { LocalTime } from "@/components/garage/local-time";
import {
  formatDistance,
  formatPercent,
  formatPowerKw,
  type Quantity,
  type UnitSystem,
} from "@/lib/units";
import { vehicleTitle, type VehicleState, type VehicleSummary } from "@/lib/vehicles";

import { RefreshButton } from "./refresh-button";
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
    <div className="mx-auto max-w-5xl space-y-8">
      <PageHeader
        title={vehicleTitle(vehicle)}
        description={
          state.recorded_at ? (
            <>
              Last update <LocalTime iso={state.recorded_at} />
            </>
          ) : undefined
        }
        actions={<QuickLinks vehicleId={vehicle.id} />}
      />

      {awaiting ? (
        <div
          role="status"
          data-testid="awaiting-first-update"
          className="rounded-lg border border-border bg-muted/50 p-4 text-sm"
        >
          <p className="font-medium">Connected — awaiting first update from your vehicle.</p>
          <p className="mt-1 text-muted-foreground">
            It can take a few minutes after connecting for your car to report its battery
            state. This page will show it once it arrives.
          </p>
          <RefreshButton vehicleId={vehicle.id} className="mt-3" />
        </div>
      ) : (
        <>
          {state.is_stale ? (
            <div
              data-testid="stale-banner"
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 text-sm"
            >
              <p role="alert" className="py-1.5 text-warning">
                Data may be stale — last update{" "}
                <TimeAgo iso={state.recorded_at as string} />.
              </p>
              <RefreshButton vehicleId={vehicle.id} className="items-end" />
            </div>
          ) : null}

          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border lg:grid-cols-4">
            <StatCard
              title="Battery"
              quantity={formatPercent(signals.soc_percent)}
              recordedAt={at.soc_percent}
              meter={signals.soc_percent ?? null}
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
              powerRecordedAt={at.charging_power_kw}
            />
          </div>
        </>
      )}
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
    <section aria-label={title} className="flex flex-col gap-3 bg-background p-5">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm text-muted-foreground">{title}</h2>
        {subtitle ? <p className="text-xs text-muted-foreground">{subtitle}</p> : null}
      </div>
      <div className="flex-1 space-y-2">{children}</div>
      <p className="text-xs text-muted-foreground">
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
  meter,
}: {
  title: string;
  subtitle?: string;
  quantity: Quantity | null;
  recordedAt?: string;
  /** 0–100: draws a charge bar, colored only when the charge is low. */
  meter?: number | null;
}) {
  return (
    <Card title={title} subtitle={subtitle} recordedAt={quantity ? recordedAt : undefined}>
      {quantity ? (
        <p className="text-3xl font-semibold tracking-tight tabular-nums">
          {quantity.value}
          <span className="ml-1 text-sm font-normal text-muted-foreground">{quantity.unit}</span>
        </p>
      ) : (
        <p className="pt-2 text-sm text-muted-foreground">Not reported</p>
      )}
      {quantity && meter != null ? <ChargeMeter percent={meter} /> : null}
    </Card>
  );
}

function ChargeMeter({ percent }: { percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div aria-hidden className="h-1 overflow-hidden rounded-full bg-muted">
      <div
        className={cn(
          "h-full rounded-full",
          clamped < 15 ? "bg-destructive" : clamped < 30 ? "bg-warning" : "bg-foreground",
        )}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

/**
 * Charging status and power are separate signals that update independently,
 * so the power reading carries its own timestamp: a power value cached from an
 * earlier session must not look as fresh as the current status.
 */
function ChargingCard({
  isCharging,
  powerKw,
  recordedAt,
  powerRecordedAt,
}: {
  isCharging: boolean | null;
  powerKw: number | null;
  recordedAt?: string;
  powerRecordedAt?: string;
}) {
  const power = formatPowerKw(powerKw);
  return (
    <Card title="Charging" recordedAt={isCharging === null ? undefined : recordedAt}>
      <p className="flex items-center gap-2 pt-1 text-base font-medium">
        {isCharging === null ? (
          <span className="text-sm font-normal text-muted-foreground">Not reported</span>
        ) : isCharging ? (
          <>
            <span aria-hidden className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-60" />
              <span className="relative inline-flex size-2 rounded-full bg-success" />
            </span>
            Charging
          </>
        ) : (
          <>
            <span aria-hidden className="size-2 rounded-full bg-muted-foreground/40" />
            Not charging
          </>
        )}
      </p>
      {isCharging ? (
        <p
          data-testid="charging-power"
          className="text-sm text-muted-foreground"
        >
          {power ? (
            <>
              {`${power.value} ${power.unit}`}
              {powerRecordedAt ? (
                <span className="text-xs text-muted-foreground">
                  {" "}
                  · as of <LocalTime iso={powerRecordedAt} />
                </span>
              ) : null}
            </>
          ) : (
            "Charging power unavailable"
          )}
        </p>
      ) : null}
    </Card>
  );
}

function QuickLinks({ vehicleId }: { vehicleId: string }) {
  const links = [
    { href: `/garage/${vehicleId}`, label: "View Vehicle", primary: false },
    { href: "/charging", label: "Find Chargers", primary: false },
    { href: "/trips", label: "Plan Trip", primary: true },
  ];
  return (
    <nav aria-label="Quick links" className="flex flex-wrap gap-2">
      {links.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          className={buttonVariants({ variant: link.primary ? "default" : "outline", size: "sm" })}
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
      className="mx-auto flex max-w-xl flex-col items-center gap-3 rounded-lg border border-dashed border-border px-6 py-14 text-center"
    >
      <span className="flex size-10 items-center justify-center rounded-full border border-border">
        <CarFront aria-hidden className="size-5 text-muted-foreground" strokeWidth={1.75} />
      </span>
      <h1 className="text-lg font-semibold tracking-tight">No vehicle connected</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        Connect your EV in the Garage to see its battery level, range and charging status here.
      </p>
      <Link href="/garage" className={cn(buttonVariants({ size: "sm" }), "mt-2")}>
        Go to Garage
        <ArrowUpRight aria-hidden />
      </Link>
    </div>
  );
}
