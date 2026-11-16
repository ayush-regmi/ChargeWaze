"use client";

import { useState, useTransition } from "react";

import { setPrimaryVehicle } from "@/app/(app)/garage/actions";
import { Button } from "@/components/ui/button";

/** "Set as primary": the Dashboard then shows this vehicle (api §3.1a). */
export function SetPrimaryButton({ vehicleId }: { vehicleId: string }) {
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function onClick() {
    setError(null);
    startTransition(async () => {
      const result = await setPrimaryVehicle(vehicleId);
      if (result?.error) setError(result.error);
    });
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant="outline" size="sm" onClick={onClick} disabled={pending}>
        {pending ? "Saving…" : "Set as primary"}
      </Button>
      {error ? (
        <p role="alert" className="text-xs text-red-700 dark:text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function PrimaryBadge() {
  return (
    <span className="rounded-md bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
      Primary
    </span>
  );
}
