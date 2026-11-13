"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { safeNextPath } from "@/lib/safe-next";
import { createClient } from "@/lib/supabase/browser";
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
      <form onSubmit={onSubmit} className="space-y-3">
        <label className="block text-sm">
          <span className="mb-1 block text-neutral-700 dark:text-neutral-300">
            Email
          </span>
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 dark:border-neutral-700 dark:bg-neutral-950"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-neutral-700 dark:text-neutral-300">
            Password
          </span>
          <input
            type="password"
            autoComplete={mode === "sign-in" ? "current-password" : "new-password"}
            minLength={8}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 dark:border-neutral-700 dark:bg-neutral-950"
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className={cn(
            "flex w-full items-center justify-center rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-sm",
            "hover:bg-emerald-700 disabled:opacity-60",
          )}
        >
          {submitting ? "Working..." : mode === "sign-in" ? "Sign in" : "Sign up"}
        </button>
      </form>

      {(googleEnabled || appleEnabled) && (
        <>
          <div className="flex items-center gap-2 text-xs text-neutral-500">
            <span className="h-px flex-1 bg-neutral-200 dark:bg-neutral-800" />
            or continue with
            <span className="h-px flex-1 bg-neutral-200 dark:bg-neutral-800" />
          </div>
          <div className="grid grid-cols-1 gap-2">
            {googleEnabled && (
              <button
                type="button"
                onClick={() => onOAuth("google")}
                className="flex items-center justify-center gap-2 rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-800 shadow-sm hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"
              >
                Continue with Google
              </button>
            )}
            {appleEnabled && (
              <button
                type="button"
                onClick={() => onOAuth("apple")}
                className="flex items-center justify-center gap-2 rounded-md border border-neutral-300 bg-black px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-neutral-800"
              >
                Continue with Apple
              </button>
            )}
          </div>
        </>
      )}

      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      {info && (
        <p role="status" className="text-sm text-emerald-700 dark:text-emerald-300">
          {info}
        </p>
      )}
    </div>
  );
}
