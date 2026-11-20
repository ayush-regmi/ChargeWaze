import { createHmac, randomUUID } from "node:crypto";

import { expect } from "@playwright/test";

import { E2E_USER, signJwt } from "./support/e2e-auth.mjs";
import { API_ENV, API_URL } from "./support/e2e-servers";

/**
 * Phase 5 spec helpers (P5-F6 analytics, P5-F8 planned vs actual): feed the
 * running API signed Smartcar `VEHICLE_STATE` webhooks, so legs come from the
 * real ingest → post-ingest hook → inference → matching pipeline (ADR 0009),
 * and call the API as the E2E user. Nothing is written to the database
 * directly. Not a spec file, and not part of the E2E harness (`support/`).
 */

// Must match services/api/scripts/e2e_backend.py.
const SMARTCAR_VEHICLE_ID = "e2e-smartcar-vehicle";
const SMARTCAR_USER_ID = "e2e-smartcar-user";
export const MINUTE = 60_000;
export const DAY = 24 * 60 * MINUTE;

/** One reading time. Signals left out aren't sent. */
export type Reading = {
  at: number;
  odometerKm?: number;
  socPercent?: number;
  charging?: boolean;
};

function signal(code: string, body: Record<string, unknown>, at: number) {
  return {
    code,
    name: code,
    group: code.split("-")[0],
    body,
    meta: { oemUpdatedAt: at, retrievedAt: at },
  };
}

/** POST one signed VEHICLE_STATE webhook carrying `r`. */
export async function deliver(r: Reading): Promise<void> {
  const signals = [];
  if (r.odometerKm !== undefined) {
    signals.push(
      signal("odometer-traveleddistance", { unit: "km", value: r.odometerKm }, r.at),
    );
  }
  if (r.socPercent !== undefined) {
    signals.push(
      signal(
        "tractionbattery-stateofcharge",
        { unit: "percent", value: r.socPercent },
        r.at,
      ),
    );
  }
  if (r.charging !== undefined) {
    signals.push(signal("charge-ischarging", { value: r.charging }, r.at));
  }
  const payload = {
    eventId: `e2e-phase5-${randomUUID()}`,
    eventType: "VEHICLE_STATE",
    data: {
      user: { id: SMARTCAR_USER_ID },
      vehicle: {
        id: SMARTCAR_VEHICLE_ID,
        make: "TOYOTA",
        model: "bZ XLE AWD",
        year: 2026,
      },
      signals,
      triggers: [
        {
          type: "SIGNAL_UPDATED",
          signal: { code: signals[0]?.code ?? "odometer-traveleddistance" },
        },
      ],
    },
    meta: {
      deliveryId: `delivery-${randomUUID()}`,
      deliveredAt: r.at,
      webhookId: "e2e",
      version: "4.0",
      mode: "LIVE",
    },
  };
  const body = JSON.stringify(payload);
  const signature = createHmac("sha256", API_ENV.SMARTCAR_MANAGEMENT_TOKEN)
    .update(body)
    .digest("hex");
  const response = await fetch(`${API_URL}/api/v1/webhooks/smartcar`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "SC-Signature": signature },
    body,
  });
  expect(response.status, await response.clone().text()).toBe(200);
}

/** Call the API as the E2E user (the same identity the browser session has). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function apiAs(path: string, init: RequestInit = {}): Promise<any> {
  const now = Math.floor(Date.now() / 1000);
  const token = signJwt({
    sub: E2E_USER.id,
    email: E2E_USER.email,
    aud: "authenticated",
    role: "authenticated",
    iat: now,
    exp: now + 600,
  });
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  });
  expect(response.status, await response.clone().text()).toBe(200);
  return response.json();
}

/** Midday UTC today, so drives stay on one local day in any zone near UTC. */
export function todayNoonUtc(): number {
  const d = new Date();
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 12);
}
