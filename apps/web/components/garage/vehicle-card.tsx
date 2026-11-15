import Link from "next/link";

import { signalLabel, vehicleTitle, type VehicleSummary } from "@/lib/vehicles";

import { DisconnectButton } from "./disconnect-button";
import { FreshnessBadge } from "./freshness-badge";

export function SignalList({ signals }: { signals: string[] }) {
  if (signals.length === 0) {
    return (
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        Supported data will appear once the vehicle sends its first update.
      </p>
    );
  }
  return (
    <ul aria-label="Supported data" className="flex flex-wrap gap-1.5">
      {signals.map((code) => (
        <li
          key={code}
          className="rounded-md bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
        >
          {signalLabel(code)}
        </li>
      ))}
    </ul>
  );
}

export function VehicleCard({ vehicle }: { vehicle: VehicleSummary }) {
  const title = vehicleTitle(vehicle);
  const connected = vehicle.connection_status === "connected";
  return (
    <article
      aria-label={title}
      className="space-y-3 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">
            <Link href={`/garage/${vehicle.id}`} className="hover:underline">
              {title}
            </Link>
          </h2>
          <p className="text-sm capitalize text-neutral-600 dark:text-neutral-400">
            {vehicle.connection_status}
          </p>
        </div>
        {connected ? <DisconnectButton vehicleId={vehicle.id} vehicleName={title} /> : null}
      </div>
      <FreshnessBadge vehicle={vehicle} />
      <SignalList signals={vehicle.supported_signals} />
    </article>
  );
}

export function EmptyGarage() {
  return (
    <div className="rounded-lg border border-dashed border-neutral-300 p-8 text-center dark:border-neutral-700">
      <p className="font-medium">No vehicles yet</p>
      <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
        Use <span className="font-medium">Add vehicle</span> to connect your EV through Smartcar.
      </p>
    </div>
  );
}
