"use client";

import { LocateFixed } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

type GeoError = "denied" | "unavailable" | "unsupported";

function errorText(e: GeoError): string {
  if (e === "denied") return "Location access was blocked. Search for a place instead.";
  if (e === "unsupported") return "This browser can't share its location.";
  return "Couldn't get your location. Search for a place instead.";
}

/**
 * Opt-in browser geolocation (D12). The browser only asks for permission when
 * the user presses the button. The position is passed to the caller and sent
 * to our API in the request; nothing stores it.
 */
export function UseMyLocation({
  onLocate,
  label = "Use my location",
}: {
  onLocate: (point: { lat: number; lon: number }) => void;
  label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<GeoError | null>(null);

  const locate = () => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setError("unsupported");
      return;
    }
    setBusy(true);
    setError(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setBusy(false);
        onLocate({ lat: pos.coords.latitude, lon: pos.coords.longitude });
      },
      (err) => {
        setBusy(false);
        setError(err.code === err.PERMISSION_DENIED ? "denied" : "unavailable");
      },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 300_000 },
    );
  };

  return (
    <div className="space-y-1">
      <Button type="button" variant="outline" onClick={locate} disabled={busy}>
        <LocateFixed aria-hidden />
        {busy ? "Locating…" : label}
      </Button>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {errorText(error)}
        </p>
      ) : null}
    </div>
  );
}
