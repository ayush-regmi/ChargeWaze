import { redirect } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { PreferencesEditor } from "@/components/profile/preferences-editor";
import { createApi } from "@/lib/api";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

export default function ProfilePage() {
  return (
    <Suspense
      fallback={<p className="text-sm text-muted-foreground">Loading profile…</p>}
    >
      <ProfileDetails />
    </Suspense>
  );
}

async function ProfileDetails() {
  await connection();
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    redirect("/sign-in");
  }

  const api = createApi({ accessToken: session.access_token });
  const { data: me, error } = await api.GET("/api/v1/me");

  if (!me) {
    const detail = error ? JSON.stringify(error) : "unknown error";
    return (
      <section className="mx-auto max-w-2xl space-y-4">
        <h1 className="text-2xl font-semibold">Profile</h1>
        <p className="text-sm text-destructive">Backend request failed: {detail}</p>
      </section>
    );
  }

  const createdAt = new Date(me.created_at).toLocaleString();

  return (
    <section className="mx-auto max-w-2xl space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold">Profile</h1>
        <p className="text-muted-foreground">
          Your account and the defaults the trip planner uses.
        </p>
      </div>
      <dl className="grid grid-cols-1 gap-4 rounded-lg border border-border bg-background p-6 sm:grid-cols-2">
        <Field label="Display name" value={me.display_name ?? "\u2014"} />
        <Field label="Email" value={me.email ?? "\u2014"} />
        <Field label="Profile id" value={me.id} mono />
        <Field label="Created at" value={createdAt} />
      </dl>
      <PreferencesEditor preferences={me.preferences} />
      <SignOutButton />
    </section>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">
        {label}
      </dt>
      <dd
        className={`mt-1 text-sm ${mono ? "font-mono text-xs break-all" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}
