"use client";

import { useState, useTransition } from "react";

import { setPrimaryVehicle } from "@/app/(app)/garage/actions";
import { Badge } from "@/components/ui/badge";
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
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function PrimaryBadge() {
  return (
    <Badge tone="outline" className="text-foreground">
      Primary
    </Badge>
  );
}
