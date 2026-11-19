"use client";

import { useState, useTransition } from "react";

import { disconnectVehicle } from "@/app/(app)/garage/actions";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

/**
 * Disconnect with a confirm step. On success the server action redirects to
 * the Garage, which re-renders without the vehicle; on failure the dialog
 * stays open with the error.
 */
export function DisconnectButton({
  vehicleId,
  vehicleName,
}: {
  vehicleId: string;
  vehicleName: string;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function confirm() {
    setError(null);
    startTransition(async () => {
      const result = await disconnectVehicle(vehicleId);
      if (result?.error) setError(result.error);
    });
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (pending) return;
        setOpen(next);
        if (!next) setError(null);
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="outline" size="sm">
          Disconnect
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Disconnect {vehicleName}?</AlertDialogTitle>
          <AlertDialogDescription>
            Chargewise will stop receiving data from this vehicle and revoke its Smartcar
            access. Its history is kept, and you can connect it again later.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <Button variant="destructive" onClick={confirm} disabled={pending}>
            {pending ? "Disconnecting…" : "Disconnect"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
