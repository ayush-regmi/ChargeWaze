import { CarFront } from "lucide-react";
import Link from "next/link";

import { signalLabel, vehicleTitle, type VehicleSummary } from "@/lib/vehicles";

import { DisconnectButton } from "./disconnect-button";
import { FreshnessBadge } from "./freshness-badge";
import { PrimaryBadge, SetPrimaryButton } from "./set-primary-button";

export function SignalList({ signals }: { signals: string[] }) {
  if (signals.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Supported data will appear once the vehicle sends its first update.
      </p>
    );
  }
  return (
    <ul aria-label="Supported data" className="flex flex-wrap gap-1.5">
      {signals.map((code) => (
        <li
          key={code}
          className="rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground"
        >
          {signalLabel(code)}
        </li>
      ))}
    </ul>
  );
}

export function VehicleCard({
  vehicle,
  isPrimary = false,
}: {
  vehicle: VehicleSummary;
  /** True when this is the user's chosen primary vehicle (shown on the Dashboard). */
  isPrimary?: boolean;
}) {
  const title = vehicleTitle(vehicle);
  const connected = vehicle.connection_status === "connected";
  return (
    <article
      aria-label={title}
      className="space-y-4 rounded-lg border border-border bg-card p-5 transition-colors hover:border-foreground/20"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold tracking-tight">
              <Link href={`/garage/${vehicle.id}`} className="underline-offset-[3px] hover:underline">
                {title}
              </Link>
            </h2>
            {isPrimary ? <PrimaryBadge /> : null}
          </div>
          <p className="flex items-center gap-1.5 text-sm capitalize text-muted-foreground">
            <span
              aria-hidden
              className={connected ? "size-1.5 rounded-full bg-success" : "size-1.5 rounded-full bg-muted-foreground/40"}
            />
            {vehicle.connection_status}
          </p>
        </div>
        {connected ? (
          <div className="flex flex-wrap items-start gap-2">
            {isPrimary ? null : <SetPrimaryButton vehicleId={vehicle.id} />}
            <DisconnectButton vehicleId={vehicle.id} vehicleName={title} />
          </div>
        ) : null}
      </div>
      <FreshnessBadge vehicle={vehicle} />
      <SignalList signals={vehicle.supported_signals} />
    </article>
  );
}

export function EmptyGarage() {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed border-border px-6 py-14 text-center">
      <span className="mb-3 flex size-10 items-center justify-center rounded-full border border-border">
        <CarFront aria-hidden className="size-5 text-muted-foreground" strokeWidth={1.75} />
      </span>
      <p className="font-medium">No vehicles yet</p>
      <p className="mt-1 text-sm text-muted-foreground">
        Use <span className="font-medium">Add vehicle</span> to connect your EV through Smartcar.
      </p>
    </div>
  );
}
