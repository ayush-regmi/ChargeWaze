import { redirect } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { createApi } from "@/lib/api";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

type Preferences = {
  units: string;
  reserve_soc: number;
  preferred_charge_to: number;
  max_detour_minutes: number;
  preferred_networks: string[];
  primary_vehicle_id: string | null;
};

type MeBody = {
  id: string;
  display_name: string | null;
  email: string | null;
  created_at: string;
  preferences: Preferences;
};

export default function ProfilePage() {
  return (
    <Suspense
      fallback={<p className="text-sm text-neutral-500">Loading profile…</p>}
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
  const result = await api.GET("/api/v1/me");

  // openapi-fetch returns a discriminated union { data } | { error }. Narrow
  // with `in` so TypeScript selects the right branch cleanly.
  if (!("data" in result) || result.data === undefined) {
    const detail =
      "error" in result ? JSON.stringify(result.error) : "unknown error";
    return (
      <section className="mx-auto max-w-2xl space-y-4">
        <h1 className="text-2xl font-semibold">Profile</h1>
        <p className="text-sm text-red-600">Backend request failed: {detail}</p>
      </section>
    );
  }

  const me = result.data as unknown as MeBody;
  const createdAt = new Date(me.created_at).toLocaleString();

  return (
    <section className="mx-auto max-w-2xl space-y-6">
      <div className="space-y-2">
        <span className="inline-block rounded-full bg-emerald-600/10 px-3 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
          Phase 1 slice 2
        </span>
        <h1 className="text-2xl font-semibold">Profile</h1>
        <p className="text-neutral-600 dark:text-neutral-400">
          Signed in via Supabase Auth. Data below comes from{" "}
          <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
            GET /api/v1/me
          </code>
          .
        </p>
      </div>
      <dl className="grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-900 sm:grid-cols-2">
        <Field label="Display name" value={me.display_name ?? "\u2014"} />
        <Field label="Email" value={me.email ?? "\u2014"} />
        <Field label="Profile id" value={me.id} mono />
        <Field label="Created at" value={createdAt} />
        <Field label="Units" value={me.preferences.units} />
        <Field
          label="Reserve SoC"
          value={`${(me.preferences.reserve_soc * 100).toFixed(0)}%`}
        />
        <Field
          label="Preferred charge-to"
          value={`${(me.preferences.preferred_charge_to * 100).toFixed(0)}%`}
        />
        <Field
          label="Max detour"
          value={`${me.preferences.max_detour_minutes} minutes`}
        />
      </dl>
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
      <dt className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
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
