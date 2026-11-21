"""Fixtures for the pure optimizer tests (Phase 4 F3). No database, no HTTP."""

from __future__ import annotations

import itertools
import json
import math
import uuid
from pathlib import Path

import pytest

from app.core.geo import EARTH_RADIUS_KM, LatLon, encode_polyline, haversine_km
from app.integrations.stations.afdc import normalize_station
from app.modules.energy import EnergyVehicle
from app.modules.routing.plan_types import CandidateStation, PlannerSettings, RouteProfile
from tests.energy.conftest import bz_vehicle

ROOT = Path(__file__).resolve().parents[4]
AFDC_FIXTURES = ROOT / "fixtures" / "stations" / "afdc"
MAPBOX_FIXTURES = ROOT / "fixtures" / "mapbox"

# The 2026 bZ XLE AWD's connectors (phase4_plan.md §2.3).
BZ_CONNECTORS = frozenset({"NACS", "CCS1", "J1772"})


def default_settings(**overrides: float) -> PlannerSettings:
    """phase4_plan.md §5 defaults: reserve 0.15, charge-to 0.80, overhead 5 min,
    road factor 1.3, detour speed 50 km/h, 1 % grid."""

    values: dict[str, float] = {
        "reserve_soc": 0.15,
        "charge_to_soc": 0.80,
        "stop_overhead_minutes": 5.0,
        "detour_road_factor": 1.3,
        "detour_speed_kmh": 50.0,
        "soc_grid_step": 0.01,
    }
    values.update(overrides)
    return PlannerSettings(**values)


def station(
    name: str,
    along_km: float,
    off_km: float,
    power_kw: float,
    connector: str = "CCS1",
) -> CandidateStation:
    """A synthetic candidate with a stable id derived from its name."""

    return CandidateStation(
        station_id=uuid.uuid5(uuid.NAMESPACE_URL, f"chargewaze-test-station:{name}"),
        name=name,
        network=None,
        connector=connector,
        power_kw=power_kw,
        distance_along_route_km=along_km,
        distance_from_route_km=off_km,
        lat=0.0,
        lon=0.0,
    )


def frederick_toronto_route() -> RouteProfile:
    """Real Mapbox route A distance and duration (748,107.9 m, 30,890.7 s)."""

    body = json.loads((MAPBOX_FIXTURES / "directions_frederick_toronto.json").read_bytes())
    route = body["routes"][0]
    return RouteProfile(
        distance_km=route["distance"] / 1000.0, duration_minutes=route["duration"] / 60.0
    )


def frederick_toronto_candidates(max_detour_minutes: float = 20.0) -> list[CandidateStation]:
    """Every DC station in the real AFDC along-route fixture for route A, mapped
    the way F4 will: a bZ-compatible connector with known power (D12, highest
    known power wins), positioned on the simplified route sent to AFDC, and
    within the detour limit (D4)."""

    route = _simplified_route()
    raw = json.loads((AFDC_FIXTURES / "nearby_route_cross_border_dc_fast.json").read_bytes())
    settings = default_settings()
    candidates: list[CandidateStation] = []
    for item in raw["fuel_stations"]:
        s = normalize_station(item)
        usable = [c for c in s.connectors if c.type in BZ_CONNECTORS and c.power_kw is not None]
        if not usable:
            continue
        best = max(usable, key=lambda c: c.power_kw or 0.0)
        assert best.power_kw is not None
        along, off = _project(route, (s.lat, s.lon))
        detour_minutes = 2 * off * settings.detour_road_factor / settings.detour_speed_kmh * 60
        if detour_minutes > max_detour_minutes:
            continue
        candidates.append(
            CandidateStation(
                station_id=uuid.uuid5(uuid.NAMESPACE_URL, f"afdc:{s.external_id}"),
                name=s.name,
                network=s.network,
                connector=best.type,
                power_kw=best.power_kw,
                distance_along_route_km=along,
                distance_from_route_km=off,
                lat=s.lat,
                lon=s.lon,
            )
        )
    return candidates


def _simplified_route() -> list[LatLon]:
    text = (AFDC_FIXTURES / "route_frederick_toronto_simplified.wkt").read_text().strip()
    body = text[text.index("(") + 1 : text.rindex(")")]
    points = []
    for pair in body.split(","):
        lon, lat = (float(v) for v in pair.split())
        points.append((lat, lon))
    return points


def _project(route: list[LatLon], point: LatLon) -> tuple[float, float]:
    """``(along_km, off_km)`` of ``point`` on ``route``: nearest segment in a local
    equirectangular projection, along = route length up to the projected point."""

    km_per_deg = math.pi / 180 * EARTH_RADIUS_KM
    best: tuple[float, float] | None = None
    along = 0.0
    for a, b in itertools.pairwise(route):
        length = haversine_km(a, b)
        kx = math.cos(math.radians((a[0] + b[0]) / 2)) * km_per_deg
        dx, dy = (b[1] - a[1]) * kx, (b[0] - a[0]) * km_per_deg
        px, py = (point[1] - a[1]) * kx, (point[0] - a[0]) * km_per_deg
        len_sq = dx * dx + dy * dy
        t = 0.0 if len_sq == 0 else max(0.0, min(1.0, (px * dx + py * dy) / len_sq))
        off = math.hypot(px - t * dx, py - t * dy)
        if best is None or off < best[1]:
            best = (along + t * length, off)
        along += length
    assert best is not None
    return best


@pytest.fixture
def bz() -> EnergyVehicle:
    return bz_vehicle()


@pytest.fixture
def settings() -> PlannerSettings:
    return default_settings()


# ---------------------------------------------------------------------------
# POST /routes/plan (Phase 4 F4): synthetic straight routes and stations.
# ---------------------------------------------------------------------------

# A route due north from here: a meridian is a great circle, so the simplified
# line (its two endpoints) has exactly the haversine length, and a station's
# distance along the route is its latitude offset.
SYNTHETIC_ORIGIN: LatLon = (39.0, -77.0)


def north_of(along_km: float, off_km: float = 0.0) -> LatLon:
    """The point ``along_km`` north of ``SYNTHETIC_ORIGIN`` and ``off_km`` east."""

    km_per_deg = math.pi / 180 * EARTH_RADIUS_KM
    lat = SYNTHETIC_ORIGIN[0] + along_km / km_per_deg
    lon = SYNTHETIC_ORIGIN[1] + off_km / (km_per_deg * math.cos(math.radians(lat)))
    return (lat, lon)


def synthetic_directions(distance_km: float, duration_minutes: float) -> dict[str, object]:
    """A Mapbox Directions body for one straight route north of the origin."""

    steps = max(2, int(distance_km))
    points = [north_of(distance_km * i / steps) for i in range(steps + 1)]
    return {
        "code": "Ok",
        "routes": [
            {
                "geometry": encode_polyline(points),
                "distance": haversine_km(points[0], points[-1]) * 1000.0,
                "duration": duration_minutes * 60.0,
                "legs": [{"summary": "Synthetic Rd", "admins": [{"iso_3166_1": "US"}]}],
            }
        ],
    }
