import { Suspense } from "react";

import { ConnectBanner } from "@/components/garage/connect-banner";

import { startVehicleConnect } from "./actions";

export default function GaragePage() {
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">My Garage</h1>
          <p className="text-neutral-600 dark:text-neutral-400">
            Connect your EV through Smartcar so Chargewise can read its battery
            level, range and charging status.
          </p>
        </div>
        <form action={startVehicleConnect}>
          <button
            type="submit"
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-emerald-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
          >
            Add vehicle
          </button>
        </form>
      </div>
      <Suspense>
        <ConnectBanner />
      </Suspense>
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        Your connected vehicles will be listed here.
      </p>
    </section>
  );
}
