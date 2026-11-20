"use client";

import { useId, useState, useTransition } from "react";

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
import { fieldClass, labelClass } from "@/components/ui/field";

import { DELETE_CONFIRMATION } from "./privacy";
import { deleteAccount } from "./privacy-actions";

/**
 * Profile "Delete account" (PRD §4.6, api §3.1b). The confirm button stays
 * disabled until the user types DELETE. On success the server action signs
 * out and redirects to sign-in; on failure the dialog stays open with the error.
 */
export function DeleteAccount() {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const inputId = useId();

  const confirmed = typed.trim() === DELETE_CONFIRMATION;

  function confirm() {
    if (!confirmed) return;
    setError(null);
    startTransition(async () => {
      const result = await deleteAccount(typed.trim());
      if (result?.error) setError(result.error);
    });
  }

  return (
    <section
      aria-label="Delete account"
      className="space-y-3 rounded-lg border border-border bg-background p-6"
    >
      <h2 className="text-sm font-medium">Delete account</h2>
      <p className="text-sm text-muted-foreground">
        Permanently removes your profile, vehicles, battery readings, planned and observed trips,
        favorites and privacy choices, disconnects your cars from Smartcar, and deletes your
        sign-in. This can&apos;t be undone.
      </p>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          if (pending) return;
          setOpen(next);
          if (!next) {
            setTyped("");
            setError(null);
          }
        }}
      >
        <AlertDialogTrigger asChild>
          <Button variant="outline">Delete account</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete your account?</AlertDialogTitle>
            <AlertDialogDescription>
              All of your Chargewise data is deleted right away and your cars are disconnected.
              You&apos;ll be signed out.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-1.5">
            <label htmlFor={inputId} className={labelClass}>
              Type {DELETE_CONFIRMATION} to confirm
            </label>
            <input
              id={inputId}
              className={fieldClass}
              value={typed}
              autoComplete="off"
              spellCheck={false}
              disabled={pending}
              onChange={(event) => setTyped(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") confirm();
              }}
            />
          </div>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
            <Button variant="destructive" onClick={confirm} disabled={!confirmed || pending}>
              {pending ? "Deleting…" : "Delete account"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
