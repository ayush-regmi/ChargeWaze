import Link from "next/link";

import { AuthForm } from "@/components/auth/auth-form";
import { env } from "@/lib/env";

export default function SignInPage() {
  return (
    <div className="space-y-6 rounded-xl border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold">Sign in to Chargewise</h1>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Use your email and password or a social provider.
        </p>
      </div>
      <AuthForm
        mode="sign-in"
        googleEnabled={env.googleAuthEnabled}
        appleEnabled={env.appleAuthEnabled}
      />
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        No account?{" "}
        <Link
          href="/sign-up"
          className="font-medium text-emerald-700 hover:underline dark:text-emerald-400"
        >
          Create one
        </Link>
      </p>
    </div>
  );
}
