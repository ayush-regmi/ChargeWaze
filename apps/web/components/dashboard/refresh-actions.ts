"use server";

import { revalidatePath } from "next/cache";

import { requireSessionApi } from "@/lib/session-api";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type RefreshResult =
  | {
      ok: true;
      /** New telemetry rows; 0 when the car had nothing newer. */
      inserted: number;
      isStale: boolean;
      /** Newest provider timestamp after the refresh, if any. */
      recordedAt: string | null;
    }
  | { ok: false; error: string };

type ErrorBody = { error?: { code?: string; details?: { retry_after_seconds?: number } } };

/**
 * Ask the backend to read the car once from Smartcar
 * (`POST /api/v1/vehicles/{id}/refresh`, api §3.5a, ADR 0010). Only ever
 * triggered by the user; page loads stay on the cached state.
 */
export async function refreshVehicle(vehicleId: string): Promise<RefreshResult> {
  if (!UUID_RE.test(vehicleId)) {
    return { ok: false, error: "This vehicle couldn't be found." };
  }
  const api = await requireSessionApi("/dashboard");
  let status = 0;
  let body: ErrorBody | undefined;
  let data:
    | { inserted: number; state: { is_stale: boolean; recorded_at?: string | null } }
    | undefined;
  try {
    const result = await api.POST("/api/v1/vehicles/{vehicle_id}/refresh", {
      params: { path: { vehicle_id: vehicleId } },
    });
    status = result.response.status;
    data = result.data;
    body = result.error as ErrorBody | undefined;
  } catch {
    status = 0;
  }

  if (status === 200 && data) {
    revalidatePath("/dashboard");
    revalidatePath("/garage");
    revalidatePath(`/garage/${vehicleId}`);
    return {
      ok: true,
      inserted: data.inserted,
      isStale: data.state.is_stale,
      recordedAt: data.state.recorded_at ?? null,
    };
  }
  return { ok: false, error: errorMessage(status, body) };
}

function errorMessage(status: number, body: ErrorBody | undefined): string {
  const code = body?.error?.code;
  const wait = waitText(body?.error?.details?.retry_after_seconds);
  if (code === "RATE_LIMITED") {
    return `This car was refreshed recently. You can refresh again ${wait ?? "later"}.`;
  }
  if (code === "SMARTCAR_RATE_LIMITED") {
    return `Your car's data provider is busy. Try again ${wait ?? "in a few minutes"}.`;
  }
  if (code === "VEHICLE_NOT_CONNECTED") {
    return "This car isn't connected. Reconnect it in the Garage to refresh.";
  }
  if (status === 404) return "This vehicle couldn't be found.";
  return "Couldn't reach your car's data provider. Please try again later.";
}

/** "in 12 min" / "in 2 h" for a wait in seconds, or `undefined`. */
function waitText(seconds: number | undefined): string | undefined {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return undefined;
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 90) return `in ${minutes} min`;
  return `in ${Math.round(minutes / 60)} h`;
}
