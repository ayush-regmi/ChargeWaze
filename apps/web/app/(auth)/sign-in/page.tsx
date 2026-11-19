import Link from "next/link";
import { Suspense } from "react";

import { AuthForm } from "@/components/auth/auth-form";
import { env } from "@/lib/env";

export default function SignInPage() {
  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Sign in to Chargewise</h1>
        <p className="text-sm text-muted-foreground">
          Use your email and password or a social provider.
        </p>
      </div>
      <Suspense>
        <AuthForm
          mode="sign-in"
          googleEnabled={env.googleAuthEnabled}
          appleEnabled={env.appleAuthEnabled}
        />
      </Suspense>
      <p className="text-sm text-muted-foreground">
        No account?{" "}
        <Link href="/sign-up" className="font-medium link">
          Create one
        </Link>
      </p>
    </div>
  );
}
