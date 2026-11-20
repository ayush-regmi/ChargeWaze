"use client";

import "mapbox-gl/dist/mapbox-gl.css";

import type * as GeoJSON from "geojson";
import mapboxgl from "mapbox-gl";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import { decodePolyline, toLngLat, PolylineDecodeError } from "@/lib/polyline";

import { mapboxPublicToken } from "./token";
import type { MapPoint, MapRoute, MapSegment, MapStation, MapViewProps } from "./types";

const STYLE_URL = "mapbox://styles/mapbox/streets-v12";
const ROUTES_SOURCE = "cw-routes";
const STATIONS_SOURCE = "cw-stations";
const CENTER_SOURCE = "cw-center";
const SEGMENTS_SOURCE = "cw-segments";
const ROUTE_LAYER = "cw-route-lines";
const ROUTE_SELECTED_LAYER = "cw-route-selected";
const STATION_LAYER = "cw-stations";
const STATION_LABEL_LAYER = "cw-station-labels";
const SEGMENT_LAYER = "cw-segments";

const SELECTED_ROUTE_COLOR = { default: "#2563eb", infeasible: "#9ca3af" } as const;

type Feature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, unknown>>;

function collection(features: Feature[]): GeoJSON.FeatureCollection<GeoJSON.Geometry, Record<string, unknown>> {
  return { type: "FeatureCollection", features };
}

/** Route lines as GeoJSON; a route that fails to decode is skipped, not fatal. */
export function routeFeatures(
  routes: readonly MapRoute[],
  selectedRouteIndex: number | null | undefined,
): Feature[] {
  const out: Feature[] = [];
  for (const r of routes) {
    try {
      const coords = toLngLat(decodePolyline(r.geometry_polyline6));
      if (coords.length < 2) continue;
      out.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: coords },
        properties: { index: r.index, selected: r.index === selectedRouteIndex },
      });
    } catch (e) {
      if (!(e instanceof PolylineDecodeError)) throw e;
    }
  }
  return out;
}

export function stationFeatures(
  stations: readonly MapStation[],
  selectedStationId: string | null | undefined,
): Feature[] {
  return stations.map((s) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [s.location.lon, s.location.lat] },
    properties: {
      id: s.id,
      name: s.name ?? "",
      compat: s.compatible === true ? "yes" : s.compatible === false ? "no" : "unknown",
      selected: s.id === selectedStationId,
      stop: s.kind === "stop",
      label: s.label ?? "",
    },
  }));
}

/** Highlighted stretches as lines; one with fewer than two points is skipped. */
export function segmentFeatures(segments: readonly MapSegment[]): Feature[] {
  return segments
    .filter((s) => s.coordinates.length >= 2)
    .map((s) => ({
      type: "Feature",
      geometry: { type: "LineString", coordinates: s.coordinates.map((p) => [p.lon, p.lat]) },
      properties: { id: s.id },
    }));
}

function centerFeatures(center: MapPoint | null | undefined): Feature[] {
  return center
    ? [{ type: "Feature", geometry: { type: "Point", coordinates: [center.lon, center.lat] }, properties: {} }]
    : [];
}

/** Bounds of everything drawn, or null when there is nothing. */
export function boundsOf(features: readonly Feature[]): [[number, number], [number, number]] | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const add = ([x, y]: number[]) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  };
  for (const f of features) {
    if (f.geometry.type === "Point") add(f.geometry.coordinates);
    else if (f.geometry.type === "LineString") f.geometry.coordinates.forEach(add);
  }
  return Number.isFinite(minX) ? [[minX, minY], [maxX, maxY]] : null;
}

/**
 * The Mapbox GL map. Client-only: load it through `LazyMapView`, which adds
 * the missing-token panel and the error boundary. Draws routes (selected one
 * on top; greyed and dashed when its tone is `infeasible`), station markers
 * coloured by compatibility (plan stops larger, in the route colour, with an
 * optional text label), highlighted segments in red, and a centre marker.
 */
export function MapView({
  routes = [],
  selectedRouteIndex = null,
  selectedRouteTone = "default",
  segments = [],
  onSelectRoute,
  stations = [],
  selectedStationId = null,
  onSelect,
  center = null,
  label = "Map",
  className,
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const [loaded, setLoaded] = useState(false);
  // Latest callbacks, so the click handlers bound once at load stay current.
  const onSelectRef = useRef(onSelect);
  const onSelectRouteRef = useRef(onSelectRoute);
  useEffect(() => {
    onSelectRef.current = onSelect;
    onSelectRouteRef.current = onSelectRoute;
  });

  useEffect(() => {
    const container = containerRef.current;
    const token = mapboxPublicToken();
    if (!container || !token) return;
    let map: mapboxgl.Map;
    try {
      map = new mapboxgl.Map({
        container,
        accessToken: token,
        style: STYLE_URL,
        center: [-98.5, 39.8],
        zoom: 3,
        attributionControl: true,
      });
    } catch (e) {
      // No WebGL, blocked worker, … Thrown from an effect, this reaches
      // LazyMapView's error boundary and the list view keeps working.
      throw e instanceof Error ? e : new Error("Map failed to start");
    }
    mapRef.current = map;
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => {
      map.addSource(ROUTES_SOURCE, { type: "geojson", data: collection([]) });
      map.addSource(STATIONS_SOURCE, { type: "geojson", data: collection([]) });
      map.addSource(CENTER_SOURCE, { type: "geojson", data: collection([]) });
      map.addSource(SEGMENTS_SOURCE, { type: "geojson", data: collection([]) });
      map.addLayer({
        id: ROUTE_LAYER,
        type: "line",
        source: ROUTES_SOURCE,
        filter: ["!", ["get", "selected"]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#94a3b8", "line-width": 4, "line-opacity": 0.8 },
      });
      map.addLayer({
        id: ROUTE_SELECTED_LAYER,
        type: "line",
        source: ROUTES_SOURCE,
        filter: ["get", "selected"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": SELECTED_ROUTE_COLOR.default, "line-width": 6 },
      });
      map.addLayer({
        id: SEGMENT_LAYER,
        type: "line",
        source: SEGMENTS_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#dc2626", "line-width": 7 },
      });
      map.addLayer({
        id: "cw-center",
        type: "circle",
        source: CENTER_SOURCE,
        paint: {
          "circle-radius": 7,
          "circle-color": "#111827",
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: STATION_LAYER,
        type: "circle",
        source: STATIONS_SOURCE,
        paint: {
          "circle-radius": ["case", ["get", "selected"], 9, ["get", "stop"], 8, 6],
          "circle-color": [
            "case",
            ["get", "stop"],
            SELECTED_ROUTE_COLOR.default,
            [
              "match",
              ["get", "compat"],
              "yes",
              "#16a34a",
              "no",
              "#9ca3af",
              "#0ea5e9",
            ],
          ],
          "circle-stroke-color": ["case", ["get", "selected"], "#111827", "#ffffff"],
          "circle-stroke-width": ["case", ["get", "selected"], 3, 1.5],
        },
      });

      map.addLayer({
        id: STATION_LABEL_LAYER,
        type: "symbol",
        source: STATIONS_SOURCE,
        filter: ["!=", ["get", "label"], ""],
        layout: {
          "text-field": ["get", "label"],
          "text-size": 12,
          "text-offset": [0, 1.3],
          "text-anchor": "top",
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#111827", "text-halo-color": "#ffffff", "text-halo-width": 1.5 },
      });

      map.on("click", STATION_LAYER, (e) => {
        const id = (e.features?.[0] as GeoJSON.Feature | undefined)?.properties?.id;
        if (typeof id === "string") onSelectRef.current?.(id);
      });
      for (const layer of [ROUTE_LAYER, ROUTE_SELECTED_LAYER]) {
        map.on("click", layer, (e) => {
          // A station on top of a route wins the click.
          if (map.queryRenderedFeatures(e.point, { layers: [STATION_LAYER] }).length > 0) return;
          const index = (e.features?.[0] as GeoJSON.Feature | undefined)?.properties?.index;
          if (typeof index === "number") onSelectRouteRef.current?.(index);
        });
      }
      for (const layer of [STATION_LAYER, ROUTE_LAYER, ROUTE_SELECTED_LAYER]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
      setLoaded(true);
    });

    return () => {
      mapRef.current = null;
      setLoaded(false);
      map.remove();
    };
  }, []);

  // Data updates. Fit the view only when the set of things drawn changes,
  // not when the selection changes, so selecting doesn't jump the map.
  const fitKey = JSON.stringify([
    routes.map((r) => r.index + ":" + r.geometry_polyline6.length),
    stations.map((s) => s.id),
    center,
    segments.map((s) => s.id),
  ]);
  const lastFitKey = useRef<string | null>(null);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !loaded) return;
    const r = routeFeatures(routes, selectedRouteIndex);
    const s = stationFeatures(stations, selectedStationId);
    const c = centerFeatures(center);
    const g = segmentFeatures(segments);
    (map.getSource(ROUTES_SOURCE) as mapboxgl.GeoJSONSource | undefined)?.setData(collection(r));
    (map.getSource(STATIONS_SOURCE) as mapboxgl.GeoJSONSource | undefined)?.setData(collection(s));
    (map.getSource(CENTER_SOURCE) as mapboxgl.GeoJSONSource | undefined)?.setData(collection(c));
    (map.getSource(SEGMENTS_SOURCE) as mapboxgl.GeoJSONSource | undefined)?.setData(collection(g));
    if (map.getLayer(ROUTE_SELECTED_LAYER)) {
      map.setPaintProperty(ROUTE_SELECTED_LAYER, "line-color", SELECTED_ROUTE_COLOR[selectedRouteTone]);
      map.setPaintProperty(
        ROUTE_SELECTED_LAYER,
        "line-dasharray",
        selectedRouteTone === "infeasible" ? [1.5, 1.5] : [1, 0],
      );
    }

    if (lastFitKey.current !== fitKey) {
      lastFitKey.current = fitKey;
      const bounds = boundsOf([...r, ...s, ...c, ...g]);
      if (bounds) {
        const [[x0, y0], [x1, y1]] = bounds;
        if (x0 === x1 && y0 === y1) map.easeTo({ center: [x0, y0], zoom: 12 });
        else map.fitBounds(bounds, { padding: 48, maxZoom: 14, duration: 0 });
      }
    }
  }, [
    loaded,
    routes,
    selectedRouteIndex,
    selectedRouteTone,
    stations,
    selectedStationId,
    center,
    segments,
    fitKey,
  ]);

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label={label}
      data-testid="map-view"
      className={cn("h-full min-h-72 w-full overflow-hidden rounded-lg", className)}
    />
  );
}

export default MapView;
