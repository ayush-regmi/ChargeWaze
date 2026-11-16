"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { createApi } from "@/lib/api";
import { requireSessionApi } from "@/lib/session-api";
import type { VehicleSpecsUpdate } from "@/lib/vehicle-specs";
import { createClient as createSupabaseClient } from "@/lib/supabase/server";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Start Smartcar Connect: ask the backend for an authorization URL and send
 * the browser there. Smartcar returns to ``/garage/connect/callback``, which
 * completes Connect with the signed-in user's token and redirects to
 * ``/garage?connect=…``.
 */
export async function startVehicleConnect(): Promise<never> {
  const supabase = await createSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) {
    redirect("/sign-in?next=/garage");
  }

  const api = createApi({ accessToken: session.access_token });
  const { data } = await api.POST("/api/v1/vehicles/connect", {
    body: { redirect_after: "/garage" },
  });

  if (!data) {
    redirect("/garage?connect=error");
  }
  redirect(data.authorization_url);
}

export type DisconnectResult = { error: string } | undefined;

/**
 * Disconnect a vehicle (``DELETE /api/v1/vehicles/{id}``) and go back to the
 * Garage. The backend returns 204 even if Smartcar is unreachable ("local
 * disconnect wins"), so an error here means the request itself failed.
 */
export async function disconnectVehicle(vehicleId: string): Promise<DisconnectResult> {
  if (!UUID_RE.test(vehicleId)) {
    return { error: "That vehicle couldn't be found." };
  }
  const api = await requireSessionApi("/garage");
  let ok = false;
  let notFound = false;
  try {
    const { response } = await api.DELETE("/api/v1/vehicles/{vehicle_id}", {
      params: { path: { vehicle_id: vehicleId } },
    });
    ok = response.ok;
    notFound = response.status === 404;
  } catch {
    ok = false;
  }
  if (!ok) {
    return {
      error: notFound
        ? "That vehicle couldn't be found."
        : "Couldn't disconnect the vehicle. Please try again.",
    };
  }
  revalidatePath("/garage");
  redirect("/garage");
}

export type ActionResult = { error: string } | undefined;

/**
 * Make a vehicle the Dashboard's primary (`PATCH /api/v1/me/preferences`).
 * Only connected vehicles can be primary; the backend answers 404 otherwise.
 */
export async function setPrimaryVehicle(vehicleId: string): Promise<ActionResult> {
  if (!UUID_RE.test(vehicleId)) {
    return { error: "That vehicle couldn't be found." };
  }
  const api = await requireSessionApi("/garage");
  let status = 0;
  try {
    const { response } = await api.PATCH("/api/v1/me/preferences", {
      body: { primary_vehicle_id: vehicleId },
    });
    status = response.status;
  } catch {
    status = 0;
  }
  if (status !== 200) {
    return {
      error:
        status === 404
          ? "That vehicle couldn't be found. It may have been disconnected."
          : "Couldn't set the primary vehicle. Please try again.",
    };
  }
  revalidatePath("/garage");
  revalidatePath("/dashboard");
  return undefined;
}

export type SpecsUpdateBody = Required<VehicleSpecsUpdate>;

/**
 * Save a vehicle's specs (`PATCH /api/v1/vehicles/{id}/specs`). The form has
 * already validated `body` with the same rules; a 422 here means the two
 * drifted, so the message stays generic.
 */
export async function updateVehicleSpecs(
  vehicleId: string,
  body: SpecsUpdateBody,
): Promise<ActionResult> {
  if (!UUID_RE.test(vehicleId)) {
    return { error: "That vehicle couldn't be found." };
  }
  const api = await requireSessionApi(`/garage/${vehicleId}`);
  let status = 0;
  try {
    const { response } = await api.PATCH("/api/v1/vehicles/{vehicle_id}/specs", {
      params: { path: { vehicle_id: vehicleId } },
      body,
    });
    status = response.status;
  } catch {
    status = 0;
  }
  if (status !== 200) {
    const messages: Record<number, string> = {
      404: "That vehicle couldn't be found.",
      422: "Some values weren't accepted. Check them and try again.",
    };
    return { error: messages[status] ?? "Couldn't save the specs. Please try again." };
  }
  revalidatePath(`/garage/${vehicleId}`);
  revalidatePath("/dashboard");
  return undefined;
}
