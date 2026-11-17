"""Geometry helpers (app/core/geo.py) on real Mapbox geometry from the F0 fixtures."""

from __future__ import annotations

import itertools
import json
from functools import cache
from pathlib import Path

import pytest

from app.core.geo import (
    LatLon,
    decode_polyline,
    douglas_peucker_indices,
    encode_polyline,
    haversine_km,
    point_segment_distance_km,
    simplify,
    simplify_indices,
)

DIRECTIONS_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "mapbox"
    / "directions_frederick_toronto.json"
)
FREDERICK: LatLon = (39.4143, -77.4105)
TORONTO: LatLon = (43.6532, -79.3832)


@cache
def _route_a_encoded() -> str:
    return str(json.loads(DIRECTIONS_FIXTURE.read_text())["routes"][0]["geometry"])


@cache
def _route_a() -> tuple[LatLon, ...]:
    return tuple(decode_polyline(_route_a_encoded()))


# ---- polyline -----------------------------------------------------------------


def test_decode_matches_known_route_shape() -> None:
    points = _route_a()
    assert len(points) == 9719  # maps-stations-notes.md §2.1
    assert haversine_km(points[0], FREDERICK) < 0.1
    assert haversine_km(points[-1], TORONTO) < 0.1


def test_polyline_round_trip_on_fixture_is_exact() -> None:
    encoded = _route_a_encoded()
    assert encode_polyline(decode_polyline(encoded)) == encoded


def test_encode_known_value_precision5() -> None:
    # Example from Google's polyline algorithm documentation.
    points = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert encode_polyline(points, precision=5) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5) == points


def test_encode_empty() -> None:
    assert encode_polyline([]) == ""
    assert decode_polyline("") == []


@pytest.mark.parametrize("bad", ["_p~iF~ps|U_", "_p~iF~ps|U_ulLnnqC_mqNvxq`", "a b"])
def test_decode_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        decode_polyline(bad)


# ---- distances -----------------------------------------------------------------


def test_haversine() -> None:
    # One degree along a meridian is 2πR/360 = 111.195 km.
    assert haversine_km((0.0, 0.0), (1.0, 0.0)) == pytest.approx(111.195, abs=0.001)
    # Half the equator.
    assert haversine_km((0.0, 0.0), (0.0, 180.0)) == pytest.approx(20_015.1, abs=0.1)
    assert haversine_km(FREDERICK, FREDERICK) == 0
    assert haversine_km(FREDERICK, TORONTO) == haversine_km(TORONTO, FREDERICK)


def test_point_segment_distance() -> None:
    start, end = (0.0, 0.0), (0.0, 1.0)  # along the equator, ~111 km long
    assert point_segment_distance_km((0.01, 0.5), start, end) == pytest.approx(1.11, abs=0.01)
    # Beyond the end: distance to the endpoint, not to the infinite line.
    assert point_segment_distance_km((0.0, 2.0), start, end) == pytest.approx(111.2, abs=0.2)
    # Degenerate segment.
    assert point_segment_distance_km((0.01, 0.0), start, start) == pytest.approx(1.11, abs=0.01)


# ---- simplification -------------------------------------------------------------


@pytest.mark.parametrize("tolerance_km", [0.05, 0.1])
def test_simplify_keeps_endpoints_and_stays_within_tolerance(tolerance_km: float) -> None:
    points = _route_a()
    kept = douglas_peucker_indices(points, tolerance_km)

    assert kept[0] == 0
    assert kept[-1] == len(points) - 1
    assert kept == sorted(set(kept))
    assert len(kept) < len(points) / 5  # a real reduction

    # Every dropped point is within tolerance of the segment that replaced it.
    for start, end in itertools.pairwise(kept):
        for i in range(start + 1, end):
            assert (
                point_segment_distance_km(points[i], points[start], points[end])
                <= tolerance_km + 1e-9
            )


def test_simplify_matches_f0_measurement() -> None:
    # F0 measured 611-655 points at 0.05 km on the two routes (notes §1.3).
    assert 550 <= len(douglas_peucker_indices(_route_a(), 0.05)) <= 700


@pytest.mark.parametrize("max_points", [1000, 200, 50, 2])
def test_simplify_respects_point_cap(max_points: int) -> None:
    points = _route_a()
    simplified = simplify(points, tolerance_km=0.05, max_points=max_points)
    assert 2 <= len(simplified) <= max_points
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]


def test_simplify_cap_does_not_coarsen_when_already_under() -> None:
    points = _route_a()
    assert simplify_indices(points, tolerance_km=0.1, max_points=1000) == douglas_peucker_indices(
        points, 0.1
    )


def test_simplify_small_inputs() -> None:
    assert simplify([], tolerance_km=0.1, max_points=10) == []
    assert simplify([FREDERICK], tolerance_km=0.1, max_points=10) == [FREDERICK]
    assert simplify([FREDERICK, TORONTO], tolerance_km=0.1, max_points=2) == [FREDERICK, TORONTO]


def test_simplify_drops_collinear_points() -> None:
    line = [(0.0, x / 10) for x in range(11)]
    assert simplify(line, tolerance_km=0.01, max_points=100) == [line[0], line[-1]]


@pytest.mark.parametrize(("tolerance_km", "max_points"), [(0.1, 1), (0.0, 10), (-1.0, 10)])
def test_simplify_rejects_bad_arguments(tolerance_km: float, max_points: int) -> None:
    with pytest.raises(ValueError):
        simplify([FREDERICK, TORONTO], tolerance_km=tolerance_km, max_points=max_points)
