import { safeNextPath } from "@/lib/safe-next";

export const GARAGE_PATH = "/garage";

export type ConnectFlag = "success" | "already_connected" | "denied" | "error";

/**
 * Where to send the browser after Smartcar Connect: ``path`` (re-validated,
 * falling back to the Garage) with ``connect=<flag>`` set on its query.
 */
export function connectResultUrl(
  path: string | null | undefined,
  flag: ConnectFlag,
  origin: string,
): URL {
  const target = new URL(safeNextPath(path, GARAGE_PATH), origin);
  target.searchParams.set("connect", flag);
  return target;
}
