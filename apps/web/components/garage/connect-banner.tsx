"use client";

import { useSearchParams } from "next/navigation";

import type { ConnectFlag } from "@/lib/connect";

/**
 * Banner for the ``?connect=`` flag that ``/garage/connect/callback`` adds when
 * it redirects back to the Garage (docs/api-contracts.md §3.3). Unknown
 * values render nothing, so a crafted link can't inject arbitrary text.
 */

export type { ConnectFlag };

type Banner = { tone: "success" | "info" | "error"; title: string; body: string };

const BANNERS: Record<ConnectFlag, Banner> = {
  success: {
    tone: "success",
    title: "Vehicle connected",
    body: "Smartcar authorized your vehicle. Live data can take a few minutes to arrive.",
  },
  already_connected: {
    tone: "info",
    title: "Already connected",
    body: "That vehicle is already in your garage.",
  },
  denied: {
    tone: "info",
    title: "Connection cancelled",
    body: "You didn't grant access in Smartcar, so no vehicle was added.",
  },
  error: {
    tone: "error",
    title: "Couldn't connect your vehicle",
    body: "Something went wrong while talking to Smartcar. Please try again.",
  },
};

const TONE_CLASSES: Record<Banner["tone"], string> = {
  success:
    "border-success/30 bg-success/10 text-success",
  info: "border-border bg-muted/50 text-foreground",
  error:
    "border-destructive/30 bg-destructive/10 text-destructive",
};

export function connectBanner(value: string | null | undefined): Banner | null {
  if (!value || !Object.hasOwn(BANNERS, value)) {
    return null;
  }
  return BANNERS[value as ConnectFlag];
}

export function ConnectBanner() {
  const banner = connectBanner(useSearchParams().get("connect"));
  if (!banner) {
    return null;
  }
  return (
    <div
      role={banner.tone === "error" ? "alert" : "status"}
      className={`rounded-lg border px-4 py-3 text-sm ${TONE_CLASSES[banner.tone]}`}
    >
      <p className="font-medium">{banner.title}</p>
      <p className="mt-0.5">{banner.body}</p>
    </div>
  );
}
