import Link from "next/link";

import { AuthForm } from "@/components/auth/auth-form";
import { env } from "@/lib/env";

export default function SignUpPage() {
  return (
    <div className="space-y-6 rounded-xl border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold">Create your Chargewise account</h1>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          You will receive a confirmation email if required by your project.
        </p>
      </div>
      <AuthForm
        mode="sign-up"
        googleEnabled={env.googleAuthEnabled}
        appleEnabled={env.appleAuthEnabled}
      />
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Already have an account?{" "}
        <Link
          href="/sign-in"
          className="font-medium text-emerald-700 hover:underline dark:text-emerald-400"
        >
          Sign in
        </Link>
      </p>
    </div>
  );
}
