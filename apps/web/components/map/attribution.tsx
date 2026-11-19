/**
 * Data attribution shown under every map and station list. Mapbox's own
 * attribution control stays on the map itself.
 */
export function DataAttribution() {
  return (
    <p className="text-xs text-muted-foreground">
      Map data © Mapbox © OpenStreetMap. Station data: U.S. DOE / NREL Alternative Fuels Data
      Center (includes Natural Resources Canada data).
    </p>
  );
}
