/**
 * Decoder for Google's encoded polyline format at precision 6 ("polyline6"),
 * the geometry `POST /api/v1/routes/directions` returns (api §3.15).
 *
 * Points come back in `[lat, lon]` order, the same order the encoding uses.
 * Mapbox GL wants `[lon, lat]`; use `toLngLat` for that.
 */

export type LatLon = [lat: number, lon: number];

export class PolylineDecodeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PolylineDecodeError";
  }
}

/**
 * Decode an encoded polyline. Throws `PolylineDecodeError` for characters
 * outside the encoding alphabet, a value cut off mid-way, an odd number of
 * values, or coordinates out of range.
 */
export function decodePolyline(encoded: string, precision = 6): LatLon[] {
  const factor = 10 ** precision;
  const points: LatLon[] = [];
  let index = 0;
  let lat = 0;
  let lon = 0;

  const next = (): number => {
    let result = 0;
    let shift = 0;
    let byte: number;
    do {
      if (index >= encoded.length) {
        throw new PolylineDecodeError("Polyline ends in the middle of a value.");
      }
      byte = encoded.charCodeAt(index++) - 63;
      if (byte < 0 || byte > 63) {
        throw new PolylineDecodeError("Polyline has a character outside the encoding.");
      }
      // Multiplication instead of `<<` keeps this exact past 32 bits.
      result += (byte & 0x1f) * 2 ** shift;
      shift += 5;
    } while (byte >= 0x20);
    // Zig-zag decode: odd values are negative.
    return result % 2 === 1 ? -(result + 1) / 2 : result / 2;
  };

  while (index < encoded.length) {
    lat += next();
    if (index >= encoded.length) {
      throw new PolylineDecodeError("Polyline has a latitude without a longitude.");
    }
    lon += next();
    const point: LatLon = [lat / factor, lon / factor];
    if (Math.abs(point[0]) > 90 || Math.abs(point[1]) > 180) {
      throw new PolylineDecodeError("Polyline has a coordinate out of range.");
    }
    points.push(point);
  }
  return points;
}

/** `[lat, lon]` → `[lon, lat]` for Mapbox GL / GeoJSON. */
export function toLngLat(points: readonly LatLon[]): [number, number][] {
  return points.map(([lat, lon]) => [lon, lat]);
}
