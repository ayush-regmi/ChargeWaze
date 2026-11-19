/**
 * Analytics page state ↔ URL query (phase5_plan F6). Everything the page
 * shows comes from the URL, so a view can be reloaded or shared. Invalid
 * values are dropped (the API default applies), never guessed or echoed.
 *
 * Params: `vehicle`, `from`, `to` (YYYY-MM-DD), `group_by` (day|week|month,
 * default week), `simulated=1` (include simulated data), `tz` (IANA zone from
 * the browser) and `cursor` (the trips table page).
 */

import type { GroupBy } from "./format";

export type AnalyticsParams = {
  vehicleId: string | null;
  from: string | null;
  to: string | null;
  groupBy: GroupBy;
  includeSimulated: boolean;
  tz: string | null;
  cursor: string | null;
};

type RawParams = Record<string, string | string[] | undefined>;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
// IANA names: "UTC", "America/New_York", "Etc/GMT+5", "America/Argentina/Buenos_Aires".
const TZ_RE = /^[A-Za-z][A-Za-z0-9_+-]*(?:\/[A-Za-z0-9_+-]+){0,2}$/;
// The API's cursor is opaque base64url (max 512 chars), as on the saved-trips page.
const CURSOR_RE = /^[A-Za-z0-9_=-]{1,512}$/;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function isRealDate(value: string): boolean {
  if (!DATE_RE.test(value)) return false;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

export function isValidTimeZone(value: string): boolean {
  return value.length <= 64 && TZ_RE.test(value);
}

export function parseAnalyticsParams(raw: RawParams): AnalyticsParams {
  const vehicle = first(raw.vehicle);
  const from = first(raw.from);
  const to = first(raw.to);
  const groupBy = first(raw.group_by);
  const tz = first(raw.tz);
  const cursor = first(raw.cursor);
  return {
    vehicleId: vehicle && UUID_RE.test(vehicle) ? vehicle.toLowerCase() : null,
    from: from && isRealDate(from) ? from : null,
    to: to && isRealDate(to) ? to : null,
    groupBy: groupBy === "day" || groupBy === "month" ? groupBy : "week",
    includeSimulated: first(raw.simulated) === "1",
    tz: tz && isValidTimeZone(tz) ? tz : null,
    cursor: cursor && CURSOR_RE.test(cursor) ? cursor : null,
  };
}

/** The page's query string for `params` (default values left out), with a leading `?` or "". */
export function analyticsSearch(params: Partial<AnalyticsParams>): string {
  const q = new URLSearchParams();
  if (params.vehicleId) q.set("vehicle", params.vehicleId);
  if (params.from) q.set("from", params.from);
  if (params.to) q.set("to", params.to);
  if (params.groupBy && params.groupBy !== "week")
    q.set("group_by", params.groupBy);
  if (params.includeSimulated) q.set("simulated", "1");
  if (params.tz) q.set("tz", params.tz);
  if (params.cursor) q.set("cursor", params.cursor);
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Query params for the analytics API (`vehicle_id` is required there). */
export function apiQuery(params: AnalyticsParams, vehicleId: string) {
  return {
    vehicle_id: vehicleId,
    ...(params.from ? { from: params.from } : {}),
    ...(params.to ? { to: params.to } : {}),
    tz: params.tz ?? "UTC",
    include_simulated: params.includeSimulated,
  };
}
