import Link from "next/link";
import { Suspense } from "react";

import { AuthForm } from "@/components/auth/auth-form";
import { env } from "@/lib/env";

export default function SignUpPage() {
  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Create your ChargeWaze account</h1>
        <p className="text-sm text-muted-foreground">
          You will receive a confirmation email if required by your project.
        </p>
      </div>
      <Suspense>
        <AuthForm
          mode="sign-up"
          googleEnabled={env.googleAuthEnabled}
          appleEnabled={env.appleAuthEnabled}
        />
      </Suspense>
      <p className="text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/sign-in" className="font-medium link">
          Sign in
        </Link>
      </p>
    </div>
  );
}
