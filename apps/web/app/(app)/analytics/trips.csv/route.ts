import { NextResponse, type NextRequest } from "next/server";

import { errorReason } from "@/components/analytics/errors";
import {
  analyticsSearch,
  parseAnalyticsParams,
} from "@/components/analytics/params";
import { isDemoEmail } from "@/lib/demo";
import { env } from "@/lib/env";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

/**
 * "Download CSV" on the Analytics page (contract §3.11b). The browser has no
 * API token, so this handler fetches `GET /analytics/trips.csv` with the
 * session's token and passes the file through unchanged. Only the validated
 * analytics params are forwarded. On an error the user goes back to the page
 * with a message, never to a raw JSON error.
 */
export async function GET(request: NextRequest) {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const viewer = { demo: isDemoEmail(session?.user.email) };
  const params = parseAnalyticsParams(
    Object.fromEntries(request.nextUrl.searchParams),
    viewer,
  );
  const back = (error: string) => {
    const url = new URL(
      `/analytics${analyticsSearch(params, viewer)}`,
      request.nextUrl.origin,
    );
    url.searchParams.set("export_error", error);
    return NextResponse.redirect(url, 303);
  };

  if (!session) {
    const url = new URL("/sign-in", request.nextUrl.origin);
    url.searchParams.set("next", `/analytics${analyticsSearch(params, viewer)}`);
    return NextResponse.redirect(url, 303);
  }
  if (!params.vehicleId) return back("failed");

  const query = new URLSearchParams({
    vehicle_id: params.vehicleId,
    tz: params.tz ?? "UTC",
    include_simulated: String(params.includeSimulated),
  });
  if (params.from) query.set("from", params.from);
  if (params.to) query.set("to", params.to);

  let upstream: Response;
  try {
    upstream = await fetch(
      `${env.apiBaseUrl}/api/v1/analytics/trips.csv?${query}`,
      {
        headers: { Authorization: `Bearer ${session.access_token}` },
        cache: "no-store",
      },
    );
  } catch {
    return back("failed");
  }
  if (!upstream.ok) {
    const body: unknown = await upstream.json().catch(() => null);
    return back(
      upstream.status === 422 && errorReason(body) === "EXPORT_TOO_LARGE"
        ? "too_large"
        : "failed",
    );
  }

  const headers = new Headers({
    "Content-Type":
      upstream.headers.get("content-type") ?? "text/csv; charset=utf-8",
    "Cache-Control": "no-store",
  });
  const disposition = upstream.headers.get("content-disposition");
  headers.set(
    "Content-Disposition",
    disposition ?? 'attachment; filename="chargewaze-trips.csv"',
  );
  return new NextResponse(upstream.body, { status: 200, headers });
}
