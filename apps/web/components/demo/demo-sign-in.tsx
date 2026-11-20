"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { buttonVariants } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/browser";
import { cn } from "@/lib/utils";

/**
 * "Explore the demo" on the sign-in page of the public demo (P6-F2). Signs in
 * to the shared demo account, whose credentials are public on purpose: the
 * API only ever serves it simulated data and refuses its writes, except
 * trip planning.
 */
export function DemoSignIn({
  email,
  password,
}: {
  email: string;
  password: string;
}) {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    setError(null);
    setSubmitting(true);
    try {
      const { error: err } = await createClient().auth.signInWithPassword({
        email,
        password,
      });
      if (err) throw err;
      router.push("/dashboard");
      router.refresh();
    } catch (err) {
      setError(
        err instanceof Error
          ? `The demo account couldn't sign in: ${err.message}`
          : "The demo account couldn't sign in.",
      );
      setSubmitting(false);
    }
  }

  return (
    <section
      aria-labelledby="demo-sign-in-title"
      className="space-y-3 rounded-md border border-border p-4"
    >
      <div className="space-y-1">
        <h2 id="demo-sign-in-title" className="text-sm font-semibold">
          Just looking around?
        </h2>
        <p className="text-sm text-muted-foreground">
          Explore a shared demo account with a simulated car, its trips and
          analytics. No sign-up or real vehicle needed.
        </p>
      </div>
      <button
        type="button"
        onClick={onClick}
        disabled={submitting}
        className={cn(buttonVariants({ variant: "outline" }), "w-full")}
      >
        {submitting ? "Opening the demo…" : "Explore the demo"}
      </button>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  );
}
