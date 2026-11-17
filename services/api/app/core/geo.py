"""Small geometry helpers: polyline6, haversine and Douglas-Peucker simplification.

Pure math with no vendor code. The stations module uses it to shrink a Mapbox
route before sending it to AFDC (phase3_plan.md D6), and routing will use it in
Phase 4.

Points are ``(lat, lon)`` tuples in degrees, the same order the polyline
format encodes. Distances are kilometres.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

LatLon = tuple[float, float]

EARTH_RADIUS_KM = 6371.0088
POLYLINE6_PRECISION = 6


# ---------------------------------------------------------------------------
# Encoded polyline (Google algorithm; Mapbox ``polyline6`` uses precision 6)
# ---------------------------------------------------------------------------


def decode_polyline(encoded: str, precision: int = POLYLINE6_PRECISION) -> list[LatLon]:
    """Decode an encoded polyline into ``(lat, lon)`` points.

    Raises ``ValueError`` if the string is truncated or has invalid characters.
    """

    factor = 10**precision
    points: list[LatLon] = []
    index = 0
    lat = lon = 0
    length = len(encoded)
    while index < length:
        deltas: list[int] = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= length:
                    raise ValueError("Truncated polyline.")
                byte = ord(encoded[index]) - 63
                index += 1
                if byte < 0 or byte > 63:
                    raise ValueError("Invalid polyline character.")
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / factor, lon / factor))
    return points


def encode_polyline(points: Sequence[LatLon], precision: int = POLYLINE6_PRECISION) -> str:
    """Encode ``(lat, lon)`` points as a polyline string."""

    factor = 10**precision
    out: list[str] = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        ilat = round(lat * factor)
        ilon = round(lon * factor)
        _encode_value(ilat - prev_lat, out)
        _encode_value(ilon - prev_lon, out)
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def _encode_value(value: int, out: list[str]) -> None:
    value = ~(value << 1) if value < 0 else value << 1
    while value >= 0x20:
        out.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    out.append(chr(value + 63))


# ---------------------------------------------------------------------------
# Distances
# ---------------------------------------------------------------------------


def haversine_km(a: LatLon, b: LatLon) -> float:
    """Great-circle distance between two points."""

    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def point_segment_distance_km(point: LatLon, start: LatLon, end: LatLon) -> float:
    """Distance from ``point`` to the segment ``start``-``end``.

    Uses a local equirectangular projection centred on the segment, which is
    accurate to well under 1% for the short segments of a road polyline.
    """

    lat0 = math.radians((start[0] + end[0]) / 2)
    kx = math.cos(lat0) * math.pi / 180 * EARTH_RADIUS_KM
    ky = math.pi / 180 * EARTH_RADIUS_KM

    ax, ay = start[1] * kx, start[0] * ky
    bx, by = end[1] * kx, end[0] * ky
    px, py = point[1] * kx, point[0] * ky

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ---------------------------------------------------------------------------
# Douglas-Peucker simplification (phase3_plan.md D6)
# ---------------------------------------------------------------------------

# Each retry doubles the tolerance; 60 doublings of any positive tolerance
# exceeds the Earth's circumference, which always leaves just the endpoints.
_MAX_TOLERANCE_DOUBLINGS = 60


def douglas_peucker_indices(points: Sequence[LatLon], tolerance_km: float) -> list[int]:
    """Indices of the points Douglas-Peucker keeps, in order.

    Every dropped point lies within ``tolerance_km`` of the segment between the
    kept points on either side of it. The first and last points are always
    kept. Iterative, so 10,000-point routes don't hit the recursion limit.
    """

    if tolerance_km < 0:
        raise ValueError("tolerance_km must be >= 0.")
    n = len(points)
    if n <= 2:
        return list(range(n))

    keep = [False] * n
    keep[0] = keep[-1] = True
    stack: list[tuple[int, int]] = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        max_dist = -1.0
        max_index = first
        start, end = points[first], points[last]
        for i in range(first + 1, last):
            dist = point_segment_distance_km(points[i], start, end)
            if dist > max_dist:
                max_dist = dist
                max_index = i
        if max_dist > tolerance_km:
            keep[max_index] = True
            stack.append((first, max_index))
            stack.append((max_index, last))
    return [i for i, kept in enumerate(keep) if kept]


def simplify_indices(
    points: Sequence[LatLon], *, tolerance_km: float, max_points: int
) -> list[int]:
    """Douglas-Peucker at ``tolerance_km``, doubling it until at most ``max_points`` remain."""

    if max_points < 2:
        raise ValueError("max_points must be at least 2.")
    if tolerance_km <= 0:
        raise ValueError("tolerance_km must be > 0.")
    tolerance = tolerance_km
    for _ in range(_MAX_TOLERANCE_DOUBLINGS):
        indices = douglas_peucker_indices(points, tolerance)
        if len(indices) <= max_points:
            return indices
        tolerance *= 2
    raise AssertionError("unreachable")  # pragma: no cover


def simplify(points: Sequence[LatLon], *, tolerance_km: float, max_points: int) -> list[LatLon]:
    """Simplified copy of ``points`` with at most ``max_points`` points."""

    return [
        points[i]
        for i in simplify_indices(points, tolerance_km=tolerance_km, max_points=max_points)
    ]


__all__ = [
    "EARTH_RADIUS_KM",
    "LatLon",
    "decode_polyline",
    "douglas_peucker_indices",
    "encode_polyline",
    "haversine_km",
    "point_segment_distance_km",
    "simplify",
    "simplify_indices",
]
