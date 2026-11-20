"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { safeNextPath } from "@/lib/safe-next";
import { createClient } from "@/lib/supabase/browser";
import { buttonVariants } from "@/components/ui/button";
import { fieldClass, labelClass } from "@/components/ui/field";
import { cn } from "@/lib/utils";

type Mode = "sign-in" | "sign-up";

export function AuthForm({
  mode,
  googleEnabled,
  appleEnabled,
}: {
  mode: Mode;
  googleEnabled: boolean;
  appleEnabled: boolean;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = safeNextPath(searchParams.get("next"));

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setInfo(null);
    setSubmitting(true);
    try {
      const supabase = createClient();
      if (mode === "sign-in") {
        const { error: err } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (err) throw err;
        router.push(next);
        router.refresh();
      } else {
        const { data, error: err } = await supabase.auth.signUp({
          email,
          password,
          options: {
            emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
          },
        });
        if (err) throw err;
        if (data.session) {
          router.push(next);
          router.refresh();
        } else {
          setInfo("Check your email for a confirmation link.");
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onOAuth(provider: "google" | "apple") {
    setError(null);
    setInfo(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
      },
    });
    if (err) setError(err.message);
  }

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} className="space-y-4">
        <label className="block space-y-1.5">
          <span className={cn("block", labelClass)}>Email</span>
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className={cn(fieldClass, "w-full")}
          />
        </label>
        <label className="block space-y-1.5">
          <span className={cn("block", labelClass)}>Password</span>
          <input
            type="password"
            autoComplete={mode === "sign-in" ? "current-password" : "new-password"}
            minLength={8}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className={cn(fieldClass, "w-full")}
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className={cn(buttonVariants(), "w-full")}
        >
          {submitting ? "Working…" : mode === "sign-in" ? "Sign in" : "Sign up"}
        </button>
      </form>

      {(googleEnabled || appleEnabled) && (
        <>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" />
            or continue with
            <span className="h-px flex-1 bg-border" />
          </div>
          <div className="grid grid-cols-1 gap-2">
            {googleEnabled && (
              <button
                type="button"
                onClick={() => onOAuth("google")}
                className={cn(buttonVariants({ variant: "outline" }), "w-full")}
              >
                Continue with Google
              </button>
            )}
            {appleEnabled && (
              <button
                type="button"
                onClick={() => onOAuth("apple")}
                className={cn(buttonVariants({ variant: "outline" }), "w-full")}
              >
                Continue with Apple
              </button>
            )}
          </div>
        </>
      )}

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {info && (
        <p role="status" className="text-sm text-success">
          {info}
        </p>
      )}
    </div>
  );
}
