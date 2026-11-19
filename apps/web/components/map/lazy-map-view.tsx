"use client";

import dynamic from "next/dynamic";
import { Component, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { mapboxPublicToken } from "./token";
import type { MapViewProps } from "./types";

const MapView = dynamic(() => import("./map-view"), {
  ssr: false,
  loading: () => (
    <div
      aria-busy="true"
      className="flex h-full min-h-72 w-full items-center justify-center rounded-lg bg-muted text-sm text-muted-foreground"
    >
      Loading map…
    </div>
  ),
});

export function MapUnavailable({ className, reason }: { className?: string; reason?: string }) {
  return (
    <div
      role="status"
      className={cn(
        "flex h-full min-h-72 w-full flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border bg-muted/50 p-6 text-center",
        className,
      )}
    >
      <p className="text-sm font-medium">Map unavailable</p>
      <p className="text-xs text-muted-foreground">
        {reason ?? "The list still works."}
      </p>
    </div>
  );
}

class MapErrorBoundary extends Component<
  { children: ReactNode; className?: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <MapUnavailable
          className={this.props.className}
          reason="The map couldn't load in this browser. The list still works."
        />
      );
    }
    return this.props.children;
  }
}

/**
 * The map as pages use it: mapbox-gl is loaded on the client only, a missing
 * `NEXT_PUBLIC_MAPBOX_PUBLIC_TOKEN` shows a "Map unavailable" panel, and any
 * map error is caught here so the rest of the page keeps working.
 */
export function LazyMapView(props: MapViewProps) {
  if (!mapboxPublicToken()) {
    return (
      <MapUnavailable
        className={props.className}
        reason="No Mapbox public token is configured. The list still works."
      />
    );
  }
  return (
    <MapErrorBoundary className={props.className}>
      <MapView {...props} />
    </MapErrorBoundary>
  );
}
