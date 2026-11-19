/**
 * Props shared by `MapView` (the mapbox-gl component) and `LazyMapView` (the
 * client-only wrapper pages use). Kept in their own file so pages and tests
 * can import them without loading mapbox-gl.
 *
 * The props are a stable interface: the Charging page (P3-F6), the Trip
 * Planner (P3-F7) and the Phase 4 planner UI all use them as-is.
 */

export type MapPoint = { lat: number; lon: number };

export type MapRoute = {
  /** The route's `index` from `POST /routes/directions` (api §3.15). */
  index: number;
  /** Encoded polyline, precision 6, `lat,lon` order. */
  geometry_polyline6: string;
};

export type MapStation = {
  id: string;
  location: MapPoint;
  name?: string;
  /** From the stations API: true / false with a vehicle, null without one. */
  compatible?: boolean | null;
  /** `stop`: a planned charging stop, drawn larger and in the route colour. */
  kind?: "station" | "stop";
  /** Short text drawn next to the marker (e.g. "16% → 61%" on a plan stop). */
  label?: string;
};

/** A stretch of road drawn on top of the routes (e.g. a plan's limiting segment). */
export type MapSegment = {
  id: string;
  coordinates: readonly MapPoint[];
};

export type MapViewProps = {
  /** Routes to draw. The selected one is drawn on top and highlighted. */
  routes?: readonly MapRoute[];
  /** `index` of the highlighted route, or null for none. */
  selectedRouteIndex?: number | null;
  /**
   * How the selected route is styled: `infeasible` greys and dashes it, so a
   * route the planner couldn't make work is never shown as a valid one.
   */
  selectedRouteTone?: "default" | "infeasible";
  /** Highlighted stretches, drawn in red above the routes. */
  segments?: readonly MapSegment[];
  /** Called when the user clicks a route line. */
  onSelectRoute?: (index: number) => void;
  /** Station markers. */
  stations?: readonly MapStation[];
  selectedStationId?: string | null;
  /** Called with the station `id` when the user clicks a marker. */
  onSelect?: (stationId: string) => void;
  /** A search centre (or trip endpoint) marker. */
  center?: MapPoint | null;
  /** Accessible name for the map region. */
  label?: string;
  className?: string;
};
