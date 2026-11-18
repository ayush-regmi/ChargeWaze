/**
 * The public Mapbox token (tiles and styles only, URL-restricted; D3). It is
 * the only Mapbox token the browser ever sees: Directions and Search Box go
 * through the backend with the server's secret token.
 */
export function mapboxPublicToken(): string | null {
  const token = process.env.NEXT_PUBLIC_MAPBOX_PUBLIC_TOKEN?.trim();
  return token ? token : null;
}
