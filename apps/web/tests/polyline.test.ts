import { describe, expect, it } from "vitest";

import { PolylineDecodeError, decodePolyline, toLngLat, type LatLon } from "@/lib/polyline";

/** Reference encoder (Google's algorithm) to build test inputs. */
function encode(points: LatLon[], precision = 6): string {
  const factor = 10 ** precision;
  let out = "";
  let prevLat = 0;
  let prevLon = 0;
  const enc = (v: number) => {
    let n = v < 0 ? -2 * v - 1 : 2 * v;
    let s = "";
    while (n >= 0x20) {
      s += String.fromCharCode((0x20 | (n % 32)) + 63);
      n = Math.floor(n / 32);
    }
    return s + String.fromCharCode(n + 63);
  };
  for (const [lat, lon] of points) {
    const la = Math.round(lat * factor);
    const lo = Math.round(lon * factor);
    out += enc(la - prevLat) + enc(lo - prevLon);
    prevLat = la;
    prevLon = lo;
  }
  return out;
}

describe("decodePolyline", () => {
  it("decodes Google's reference example at precision 5", () => {
    expect(decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", 5)).toEqual([
      [38.5, -120.2],
      [40.7, -120.95],
      [43.252, -126.453],
    ]);
  });

  it("round-trips Frederick → Toronto points at precision 6", () => {
    const points: LatLon[] = [
      [39.4143, -77.4105],
      [41.123456, -78.654321],
      [43.6532, -79.3832],
    ];
    const decoded = decodePolyline(encode(points));
    decoded.forEach(([lat, lon], i) => {
      expect(lat).toBeCloseTo(points[i][0], 6);
      expect(lon).toBeCloseTo(points[i][1], 6);
    });
  });

  it("keeps large coordinates exact (past 32-bit intermediate values)", () => {
    const points: LatLon[] = [
      [-89.999999, 179.999999],
      [89.999999, -179.999999],
    ];
    expect(decodePolyline(encode(points))).toEqual(points);
  });

  it("returns no points for an empty string", () => {
    expect(decodePolyline("")).toEqual([]);
  });

  it("rejects malformed input", () => {
    expect(() => decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`", 5)).toThrow(PolylineDecodeError);
    expect(() => decodePolyline("_p~iF")).toThrow(PolylineDecodeError); // lat without lon
    expect(() => decodePolyline("ab c")).toThrow(PolylineDecodeError); // space is outside the alphabet
    expect(() => decodePolyline(encode([[95, 0]]))).toThrow(PolylineDecodeError);
  });
});

describe("toLngLat", () => {
  it("swaps to [lon, lat] for GeoJSON", () => {
    expect(toLngLat([[39.4, -77.4]])).toEqual([[-77.4, 39.4]]);
  });
});
