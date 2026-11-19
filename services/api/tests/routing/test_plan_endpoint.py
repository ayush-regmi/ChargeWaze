"""``POST /api/v1/routes/plan`` (Phase 4 F4, ``docs/api-contracts.md`` §3.7).

Mapbox is the real ``MapboxClient`` over ``httpx.MockTransport``. Stations come
either from the real ``AfdcProvider`` over ``MockTransport`` serving the F0
fixtures (Frederick → Toronto) or from a scripted ``StationProvider`` for the
synthetic §5 trips. No real provider is called. The database is the shared
Postgres test database, rolled back per test.
"""

from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.geo import LatLon
from app.integrations.mapbox.client import MapboxClient, get_mapbox_client
from app.integrations.stations import AfdcProvider, get_station_provider
from app.integrations.stations import errors as station_errors
from app.integrations.stations.base import (
    ProviderStation,
    StationConnector,
    StationFilters,
    StationPage,
)
from app.modules.routing.planner_service import (
    corridor_for,
    matches_preferred,
    round_minutes,
    round_soc,
)
from app.modules.trips.models import PlannedStop, PlannedTrip
from app.modules.users.models import Profile, UserPreferences
from app.modules.vehicles.models import Vehicle, VehicleSpecs
from tests.conftest import TEST_USER_ID, make_token
from tests.routing.conftest import north_of, synthetic_directions

ROOT = Path(__file__).resolve().parents[4]
MAPBOX_FIXTURES = ROOT / "fixtures" / "mapbox"
AFDC_FIXTURES = ROOT / "fixtures" / "stations" / "afdc"
PLAN = "/api/v1/routes/plan"
OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
FREDERICK = {"lat": 39.4143, "lon": -77.4105, "label": "Frederick, MD"}
TORONTO = {"lat": 43.6532, "lon": -79.3832, "label": "Toronto, ON"}
BZ_CURVE = [
    {"soc": 0.0, "power_kw": 100.0},
    {"soc": 0.1, "power_kw": 150.0},
    {"soc": 0.3, "power_kw": 150.0},
    {"soc": 0.5, "power_kw": 120.0},
    {"soc": 0.8, "power_kw": 55.0},
    {"soc": 1.0, "power_kw": 15.0},
]


def _json(path: Path) -> Any:
    return json.loads(path.read_bytes())


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeMapbox:
    """Serves one Directions body (or a scripted failure) and records requests."""

    respond: Callable[[httpx.Request], httpx.Response] = field(
        default_factory=lambda: (
            lambda _r: httpx.Response(
                200, json=_json(MAPBOX_FIXTURES / "directions_frederick_toronto.json")
            )
        )
    )
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def serve(self, body: dict[str, Any]) -> None:
        self.respond = lambda _r: httpx.Response(200, json=body)


@dataclass
class FakeAfdc:
    """AFDC ``nearby-route.json`` paged from the cross-border DC fixture."""

    fail: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(503, json={"error": "down"})
        params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        params.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        body = _json(AFDC_FIXTURES / "nearby_route_cross_border_dc_fast.json")
        offset, limit = int(params.get("offset", "0")), int(params.get("limit", "200"))
        page = copy.deepcopy(body)
        page["fuel_stations"] = body["fuel_stations"][offset : offset + limit]
        return httpx.Response(200, json=page)


@dataclass
class ScriptedStations:
    """A ``StationProvider`` returning fixed stations, for the synthetic trips."""

    stations: list[ProviderStation] = field(default_factory=list)
    fail: bool = False
    calls: list[tuple[int, float, StationFilters]] = field(default_factory=list)

    def search_along_route(
        self, points: Sequence[LatLon], corridor_km: float, filters: StationFilters
    ) -> StationPage:
        self.calls.append((len(points), corridor_km, filters))
        if self.fail:
            raise station_errors.unexpected_response()
        page = self.stations[filters.offset : filters.offset + filters.limit]
        return StationPage(stations=page, total_results=len(self.stations), offset=filters.offset)

    def search_nearby(  # pragma: no cover - not used by the planner
        self, lat: float, lon: float, radius_km: float, filters: StationFilters
    ) -> StationPage:
        raise AssertionError("the planner never searches nearby")

    def get(self, external_id: str) -> ProviderStation | None:  # pragma: no cover
        raise AssertionError("the planner never fetches one station")


def synthetic_station(
    name: str,
    along_km: float,
    off_km: float,
    power_kw: float | None,
    connector: str = "CCS1",
    network: str | None = None,
) -> ProviderStation:
    lat, lon = north_of(along_km, off_km)
    return ProviderStation(
        provider="afdc",
        external_id=f"synthetic-{name}",
        name=name,
        network=network,
        country="US",
        lat=lat,
        lon=lon,
        connectors=[StationConnector(type=connector, count=2, power_kw=power_kw)],  # type: ignore[arg-type]
        access="public",
        status="open",
        raw={},
    )


def trip2_stations() -> list[ProviderStation]:
    """phase4_plan.md §5 trip 2 candidates L, A, X, B."""

    return [
        synthetic_station("L", 120, 0.2, 50),
        synthetic_station("A", 150, 0.5, 150),
        synthetic_station("X", 200, 0.3, 150, connector="CHAdeMO"),
        synthetic_station("B", 220, 1.0, 250, connector="NACS"),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        mapbox_secret_token="sk.test-secret-token",
        mapbox_api_base_url="https://mapbox.test",
        afdc_api_key="afdc-test-key-0123456789",
        afdc_api_base_url="https://afdc.test/api/alt-fuel-stations/v1",
    )


@pytest.fixture()
def mapbox() -> FakeMapbox:
    return FakeMapbox()


@pytest.fixture()
def afdc() -> FakeAfdc:
    return FakeAfdc()


@pytest.fixture()
def scripted() -> ScriptedStations:
    return ScriptedStations()


@pytest.fixture()
def wire(
    app_with_db: FastAPI, app_settings: Settings, mapbox: FakeMapbox, afdc: FakeAfdc
) -> Iterator[Callable[..., TestClient]]:
    """``wire()`` → a client using the AFDC fixture; ``wire(provider)`` → that provider."""

    mapbox_client = MapboxClient.from_settings(app_settings, transport=httpx.MockTransport(mapbox))
    afdc_provider = AfdcProvider.from_settings(app_settings, transport=httpx.MockTransport(afdc))

    def _wire(provider: Any = None) -> TestClient:
        app_with_db.dependency_overrides[get_settings] = lambda: app_settings
        app_with_db.dependency_overrides[get_mapbox_client] = lambda: mapbox_client
        app_with_db.dependency_overrides[get_station_provider] = lambda: (
            provider if provider is not None else afdc_provider
        )
        return TestClient(app_with_db)

    try:
        yield _wire
    finally:
        mapbox_client.close()
        afdc_provider.close()


@pytest.fixture()
def db(db_connection: Connection) -> Iterator[Session]:
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def auth(key_material: Any) -> Callable[..., dict[str, str]]:
    def _auth(user_id: uuid.UUID = TEST_USER_ID) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(key_material, sub=user_id)}"}

    return _auth


@pytest.fixture()
def add_vehicle(db: Session) -> Callable[..., Vehicle]:
    """A connected vehicle. Defaults to the real bZ: catalog model, empty specs row."""

    def _add(
        *,
        user_id: uuid.UUID = TEST_USER_ID,
        model: str = "bZ XLE AWD",
        capacity: Decimal | None = None,
        connectors: list[str] | None = None,
        curve: list[dict[str, float]] | None = None,
        baseline: Decimal | None = None,
        soc_percent: float | None = None,
        soc_age: timedelta = timedelta(minutes=5),
    ) -> Vehicle:
        if db.get(Profile, user_id) is None:
            db.add(Profile(id=user_id))
            db.flush()
        latest_state: dict[str, Any] | None = None
        if soc_percent is not None:
            at = (datetime.now(UTC) - soc_age).isoformat().replace("+00:00", "Z")
            latest_state = {
                "recorded_at": at,
                "signals": {"soc": {"value": soc_percent, "unit": "percent", "recorded_at": at}},
            }
        vehicle = Vehicle(
            user_id=user_id,
            smartcar_vehicle_id=f"sc-vehicle-{uuid.uuid4()}",
            smartcar_user_id="sc-user",
            make="Toyota",
            model=model,
            year=2026,
            connection_status="connected",
            connected_at=datetime.now(UTC),
            supported_signals=[],
            latest_state=latest_state,
        )
        vehicle.specs = VehicleSpecs(
            usable_capacity_kwh=capacity,
            connectors=connectors or [],
            charging_curve=curve or [],
            baseline_kwh_per_km=baseline,
        )
        db.add(vehicle)
        db.commit()
        return vehicle

    return _add


def body(vehicle: Vehicle, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "vehicle_id": str(vehicle.id),
        "origin": FREDERICK,
        "destination": TORONTO,
        "starting_soc_override": 0.9,
    }
    payload.update(overrides)
    return payload


def synthetic_body(vehicle: Vehicle, distance_km: float, **overrides: Any) -> dict[str, Any]:
    lat, lon = north_of(distance_km)
    return body(
        vehicle,
        origin={"lat": 39.0, "lon": -77.0, "label": "Start"},
        destination={"lat": lat, "lon": lon, "label": "End"},
        **overrides,
    )


def trip_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(PlannedTrip)) or 0


# ---------------------------------------------------------------------------
# Feasible plans, persistence and latency
# ---------------------------------------------------------------------------


def test_frederick_toronto_plans_stores_and_matches_get_trip(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    afdc: FakeAfdc,
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = wire()
    vehicle = add_vehicle()

    started = time.perf_counter()
    with caplog.at_level(logging.INFO, logger="app.modules.routing.planner_service"):
        response = api.post(
            PLAN, json=body(vehicle, departure_time="2026-11-07T08:00:00Z"), headers=auth()
        )
    elapsed_ms = (time.perf_counter() - started) * 1000
    [record] = [r for r in caplog.records if r.getMessage() == "route_plan"]
    stages = {k: v for k, v in record.__dict__.items() if k.endswith("_ms")}
    print(f"\nPOST /routes/plan Frederick → Toronto (fixtures): {elapsed_ms:.0f} ms, {stages}")

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["status"] == "feasible"
    assert plan["model_version"] == "deterministic-v1"
    assert plan["starting_soc"] == 0.9
    assert plan["starting_soc_source"] == "override"
    assert plan["starting_soc_recorded_at"] is None
    assert plan["departure_time"] == "2026-11-07T08:00:00Z"
    assert plan["route"]["distance_km"] == pytest.approx(748.108, abs=1e-3)
    assert plan["route"]["drive_minutes"] == 515
    assert plan["route"]["countries"] == ["US", "CA"]
    # §5: two stops can't cover 2.107 SoC of driving from 0.90; F3 found three on this fixture.
    assert len(plan["stops"]) == 3
    assert [s["sequence"] for s in plan["stops"]] == [1, 2, 3]
    along = [s["distance_along_route_km"] for s in plan["stops"]]
    assert along == sorted(along)
    for stop in plan["stops"]:
        assert stop["arrival_soc"] >= 0.15
        assert stop["departure_soc"] <= 0.8
        assert stop["station"]["connector"] in {"NACS", "CCS1"}
        assert stop["detour_is_estimate"] is True
    assert plan["arrival_soc"] >= 0.15
    totals = plan["totals"]
    assert totals["waiting_minutes"] == 0
    assert totals["drive_minutes"] == 515
    assert totals["stop_overhead_minutes"] == 15
    parts = ("drive_minutes", "charging_minutes", "detour_minutes", "stop_overhead_minutes")
    # Totals are summed before rounding (D16), so the parts can be off by a minute.
    assert abs(totals["total_minutes"] - sum(totals[p] for p in parts)) <= 1
    assert plan["assumptions"] == {
        "usable_capacity_kwh": 71.0,
        "usable_capacity_source": "catalog",
        "baseline_kwh_per_km": 0.2,
        "baseline_source": "catalog",
        "charging_curve_source": "catalog_derived",
        "adjustments_applied": [],
        "reserve_soc": 0.15,
        "charge_to_soc": 0.8,
        "max_detour_minutes": 20,
        "preferred_networks": [],
        "preferred_networks_relaxed": False,
        "stop_overhead_minutes": 5.0,
        "detour_road_factor": 1.3,
        "detour_speed_kmh": 50.0,
        "soc_grid_step": 0.01,
    }
    candidates = plan["candidates"]
    assert candidates["searched"] is True
    assert candidates["corridor_km"] == pytest.approx(6.41, abs=1e-3)
    assert candidates["considered"] > 50
    assert plan["alternatives"] == []
    assert plan["reason"] is None
    assert afdc.requests, "the stations provider was called"

    # Stored unsaved, with its stops; GET /trips/{id} returns the same body.
    trip = api.get(f"/api/v1/trips/{plan['trip_id']}", headers=auth())
    assert trip.status_code == 200
    assert trip.json()["plan"] == plan
    assert trip.json()["saved_at"] is None
    stored_stops = db.scalars(
        select(PlannedStop)
        .where(PlannedStop.trip_id == uuid.UUID(plan["trip_id"]))
        .order_by(PlannedStop.sequence)
    ).all()
    assert [str(s.station_id) for s in stored_stops] == [s["station"]["id"] for s in plan["stops"]]
    assert [s.charging_minutes for s in stored_stops] == [
        s["charging_minutes"] for s in plan["stops"]
    ]

    # Latency on fixtures (no network): the whole request, incl. ~120 upserts.
    assert elapsed_ms < 3000


def test_synthetic_trip2_matches_hand_checked_numbers(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    """phase4_plan.md §5 trip 2 end to end: 400 km, 270 min, start 0.80 → stop at B."""

    mapbox.serve(synthetic_directions(400, 270))
    scripted.stations = trip2_stations()
    api = wire(scripted)
    vehicle = add_vehicle()

    response = api.post(
        PLAN, json=synthetic_body(vehicle, 400, starting_soc_override=0.8), headers=auth()
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["status"] == "feasible"
    [stop] = plan["stops"]
    assert stop["station"]["name"] == "B"
    assert stop["station"]["connector"] == "NACS"
    assert stop["station"]["power_kw"] == 250
    assert stop["arrival_soc"] == 0.173
    assert stop["departure_soc"] == 0.66
    assert stop["charging_minutes"] == 17
    assert stop["detour_minutes"] == 3
    assert stop["distance_along_route_km"] == pytest.approx(220, abs=0.01)
    assert stop["distance_from_route_km"] == pytest.approx(1.0, abs=0.01)
    assert plan["arrival_soc"] == 0.153
    assert plan["totals"]["total_minutes"] == 295
    assert plan["candidates"] == {
        "searched": True,
        "corridor_km": pytest.approx(6.41, abs=1e-3),
        "considered": 3,
        "excluded_incompatible": 1,
        "excluded_unknown_power": 0,
        "excluded_detour": 0,
    }
    # One along-route call, DC fast and the vehicle's connectors.
    [(_, corridor, filters)] = scripted.calls
    assert corridor == pytest.approx(6.41, abs=1e-3)
    assert filters.charging_level == "dc_fast"
    assert set(filters.connector_types) == {"NACS", "CCS1", "J1772"}


def test_short_trip_needs_no_charge_and_skips_stations(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    """§5 trip 1: 80 km, 60 min, start 0.72 → no stops, arrival 0.495."""

    mapbox.serve(synthetic_directions(80, 60))
    api = wire(scripted)
    vehicle = add_vehicle()

    response = api.post(
        PLAN, json=synthetic_body(vehicle, 80, starting_soc_override=0.72), headers=auth()
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["status"] == "feasible"
    assert plan["stops"] == []
    assert plan["arrival_soc"] == 0.495
    assert plan["totals"]["total_minutes"] == 60
    assert plan["candidates"]["searched"] is False
    assert plan["candidates"]["considered"] == 0
    assert scripted.calls == []


# ---------------------------------------------------------------------------
# Starting SoC (D7)
# ---------------------------------------------------------------------------


def test_fresh_telemetry_is_used(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
) -> None:
    mapbox.serve(synthetic_directions(80, 60))
    api = wire(ScriptedStations())
    vehicle = add_vehicle(soc_percent=72, soc_age=timedelta(minutes=5))

    response = api.post(
        PLAN, json=synthetic_body(vehicle, 80, starting_soc_override=None), headers=auth()
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["starting_soc"] == 0.72
    assert plan["starting_soc_source"] == "telemetry"
    assert plan["starting_soc_recorded_at"] is not None


def test_override_wins_over_fresh_telemetry(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
) -> None:
    mapbox.serve(synthetic_directions(80, 60))
    api = wire(ScriptedStations())
    vehicle = add_vehicle(soc_percent=40, soc_age=timedelta(minutes=1))

    response = api.post(
        PLAN, json=synthetic_body(vehicle, 80, starting_soc_override=0.95), headers=auth()
    )

    assert response.json()["starting_soc"] == 0.95
    assert response.json()["starting_soc_source"] == "override"


@pytest.mark.parametrize(
    ("soc_percent", "age", "last_soc"),
    [
        (64, timedelta(minutes=16), 0.64),  # stale: older than 15 min
        (None, timedelta(0), None),  # never reported
    ],
)
def test_stale_or_missing_soc_requires_override(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    db: Session,
    soc_percent: float | None,
    age: timedelta,
    last_soc: float | None,
) -> None:
    api = wire(ScriptedStations())
    vehicle = add_vehicle(soc_percent=soc_percent, soc_age=age)

    response = api.post(PLAN, json=body(vehicle, starting_soc_override=None), headers=auth())

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["reason"] == "STARTING_SOC_REQUIRED"
    assert error["details"]["last_soc"] == last_soc
    if last_soc is None:
        assert error["details"]["last_recorded_at"] is None
    else:
        recorded = datetime.fromisoformat(error["details"]["last_recorded_at"])
        assert datetime.now(UTC) - recorded > timedelta(minutes=15)
    assert mapbox.requests == []
    assert trip_count(db) == 0


def test_starting_soc_at_reserve_is_infeasible_without_stations(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    mapbox.serve(synthetic_directions(400, 270))
    api = wire(scripted)
    vehicle = add_vehicle()

    response = api.post(
        PLAN, json=synthetic_body(vehicle, 400, starting_soc_override=0.0), headers=auth()
    )

    plan = response.json()
    assert plan["status"] == "infeasible"
    assert plan["reason"]["code"] == "STARTING_SOC_TOO_LOW"
    assert plan["reason"]["limiting_segment"] is None
    assert plan["totals"] is None and plan["arrival_soc"] is None
    assert scripted.calls == []


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def test_request_preferences_win_over_stored_which_win_over_defaults(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    db: Session,
) -> None:
    mapbox.serve(synthetic_directions(80, 60))
    api = wire(ScriptedStations())
    vehicle = add_vehicle()
    db.add(
        UserPreferences(
            user_id=TEST_USER_ID,
            reserve_soc=Decimal("0.200"),
            preferred_charge_to=Decimal("0.900"),
            max_detour_minutes=30,
            preferred_networks=["EVgo"],
        )
    )
    db.commit()

    response = api.post(
        PLAN,
        json=synthetic_body(vehicle, 80, preferences={"reserve_soc": 0.1}),
        headers=auth(),
    )

    assumptions = response.json()["assumptions"]
    assert assumptions["reserve_soc"] == 0.1  # request
    assert assumptions["charge_to_soc"] == 0.9  # stored
    assert assumptions["max_detour_minutes"] == 30  # stored
    assert assumptions["preferred_networks"] == ["EVgo"]  # stored
    assert response.json()["candidates"]["corridor_km"] == pytest.approx(9.615, abs=1e-3)


def test_defaults_apply_for_a_new_profile(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
) -> None:
    mapbox.serve(synthetic_directions(80, 60))
    api = wire(ScriptedStations())
    vehicle = add_vehicle()  # profile without a preferences row

    assumptions = api.post(PLAN, json=synthetic_body(vehicle, 80), headers=auth()).json()[
        "assumptions"
    ]

    assert (assumptions["reserve_soc"], assumptions["charge_to_soc"]) == (0.15, 0.8)
    assert assumptions["max_detour_minutes"] == 20


def test_reserve_not_below_charge_to_after_merge_is_422(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    db: Session,
) -> None:
    api = wire(ScriptedStations())
    vehicle = add_vehicle()

    response = api.post(PLAN, json=body(vehicle, preferences={"reserve_soc": 0.8}), headers=auth())

    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"] == ["reserve_soc", "preferred_charge_to"]
    assert mapbox.requests == []
    assert trip_count(db) == 0


@pytest.mark.parametrize(
    "preferences",
    [
        {"reserve_soc": None},
        {"reserve_soc": 0.1234},
        {"preferred_networks": ["EVgo", "evgo"]},
        {"max_detour_minutes": -1},
        {"units": "metric"},
    ],
)
def test_invalid_request_preferences_are_422(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    preferences: dict[str, Any],
) -> None:
    api = wire(ScriptedStations())
    vehicle = add_vehicle()

    response = api.post(PLAN, json=body(vehicle, preferences=preferences), headers=auth())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "overrides",
    [
        {"destination": {"lat": 39.4144, "lon": -77.4105}},  # within 50 m
        {"starting_soc_override": 1.2},
        {"route_index": -1},
        {"departure_time": "2026-11-07T08:00:00"},  # naive
        {"geometry_polyline6": "abc"},  # client geometry is never accepted
        {"origin": {"lat": 39.4, "lon": -77.4, "label": "x" * 201}},
    ],
)
def test_invalid_body_is_422(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    overrides: dict[str, Any],
) -> None:
    api = wire(ScriptedStations())
    vehicle = add_vehicle()

    response = api.post(PLAN, json=body(vehicle, **overrides), headers=auth())

    assert response.status_code == 422
    assert mapbox.requests == []


# ---------------------------------------------------------------------------
# Vehicle specs (D14) and ownership
# ---------------------------------------------------------------------------


def test_vehicle_without_catalog_entry_lists_every_missing_spec(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
) -> None:
    api = wire(ScriptedStations())
    vehicle = add_vehicle(model="bZ Limited")

    response = api.post(PLAN, json=body(vehicle), headers=auth())

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details == {
        "reason": "VEHICLE_SPECS_INCOMPLETE",
        "missing": ["usable_capacity_kwh", "connectors", "charging_curve", "baseline_kwh_per_km"],
    }
    assert mapbox.requests == []


def test_user_specs_without_catalog_still_need_a_baseline(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
) -> None:
    api = wire(ScriptedStations())
    vehicle = add_vehicle(
        model="bZ Limited", capacity=Decimal("57.7"), connectors=["NACS"], curve=BZ_CURVE
    )

    response = api.post(PLAN, json=body(vehicle), headers=auth())

    assert response.json()["error"]["details"]["missing"] == ["baseline_kwh_per_km"]


def test_unusable_user_curve_is_reported_as_missing(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
) -> None:
    api = wire(ScriptedStations())
    vehicle = add_vehicle(curve=[{"soc": 0.5, "power_kw": 100.0}])  # one point

    response = api.post(PLAN, json=body(vehicle), headers=auth())

    assert response.json()["error"]["details"]["missing"] == ["charging_curve"]


def test_user_specs_override_catalog_and_are_reported_as_vehicle(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
) -> None:
    mapbox.serve(synthetic_directions(80, 60))
    api = wire(ScriptedStations())
    flat_curve = [{"soc": 0.0, "power_kw": 50.0}, {"soc": 1.0, "power_kw": 50.0}]
    vehicle = add_vehicle(capacity=Decimal("60.0"), connectors=["CCS1"], curve=flat_curve)

    response = api.post(PLAN, json=synthetic_body(vehicle, 80), headers=auth())

    assumptions = response.json()["assumptions"]
    assert assumptions["usable_capacity_kwh"] == 60.0
    assert assumptions["usable_capacity_source"] == "vehicle"
    assert assumptions["charging_curve_source"] == "vehicle"
    assert assumptions["baseline_source"] == "catalog"


def test_row_copy_of_catalog_is_reported_as_catalog(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
) -> None:
    """A newly connected bZ gets the catalog copied into its row (P4-F2)."""

    mapbox.serve(synthetic_directions(80, 60))
    api = wire(ScriptedStations())
    vehicle = add_vehicle(
        capacity=Decimal("71.0"), connectors=["NACS", "CCS1", "J1772"], curve=BZ_CURVE
    )

    assumptions = api.post(PLAN, json=synthetic_body(vehicle, 80), headers=auth()).json()[
        "assumptions"
    ]

    assert assumptions["usable_capacity_source"] == "catalog"
    assert assumptions["charging_curve_source"] == "catalog_derived"


# ---------------------------------------------------------------------------
# Per-vehicle consumption baseline (phase4_plan F8)
# ---------------------------------------------------------------------------


def test_vehicle_without_catalog_plans_once_user_sets_a_baseline(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    mapbox.serve(synthetic_directions(80, 60))
    api = wire(scripted)
    vehicle = add_vehicle(
        model="bZ Limited", capacity=Decimal("57.7"), connectors=["NACS"], curve=BZ_CURVE
    )
    plan_body = synthetic_body(vehicle, 80, starting_soc_override=0.72)

    before = api.post(PLAN, json=plan_body, headers=auth())
    assert before.status_code == 422
    assert before.json()["error"]["details"] == {
        "reason": "VEHICLE_SPECS_INCOMPLETE",
        "missing": ["baseline_kwh_per_km"],
    }

    patched = api.patch(
        f"/api/v1/vehicles/{vehicle.id}/specs",
        json={"baseline_kwh_per_km": 0.18},
        headers=auth(),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["baseline_source"] == "vehicle"

    after = api.post(PLAN, json=plan_body, headers=auth())
    assert after.status_code == 200, after.text
    plan = after.json()
    assert plan["status"] == "feasible"
    assert plan["assumptions"]["baseline_kwh_per_km"] == 0.18
    assert plan["assumptions"]["baseline_source"] == "vehicle"
    # 80 km x 0.18 kWh/km = 14.4 kWh of 57.7 kWh → 0.72 - 0.2496 ≈ 0.470.
    assert plan["arrival_soc"] == pytest.approx(0.470, abs=0.001)


def test_user_baseline_overrides_catalog(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    """Same trip as the short-trip test (arrival 0.495 on the 0.20 catalog value)."""

    mapbox.serve(synthetic_directions(80, 60))
    api = wire(scripted)
    vehicle = add_vehicle(baseline=Decimal("0.250"))

    response = api.post(
        PLAN, json=synthetic_body(vehicle, 80, starting_soc_override=0.72), headers=auth()
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["assumptions"]["baseline_kwh_per_km"] == 0.25
    assert plan["assumptions"]["baseline_source"] == "vehicle"
    # 80 km x 0.25 kWh/km = 20 kWh of 71 kWh → 0.72 - 0.2817 ≈ 0.438.
    assert plan["arrival_soc"] == pytest.approx(0.438, abs=0.001)


def test_clearing_the_user_baseline_falls_back_to_catalog(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    mapbox.serve(synthetic_directions(80, 60))
    api = wire(scripted)
    vehicle = add_vehicle(baseline=Decimal("0.250"))
    cleared = api.patch(
        f"/api/v1/vehicles/{vehicle.id}/specs",
        json={"baseline_kwh_per_km": None},
        headers=auth(),
    )
    assert cleared.status_code == 200, cleared.text

    plan = api.post(
        PLAN, json=synthetic_body(vehicle, 80, starting_soc_override=0.72), headers=auth()
    ).json()

    assert plan["assumptions"]["baseline_kwh_per_km"] == 0.2
    assert plan["assumptions"]["baseline_source"] == "catalog"
    assert plan["arrival_soc"] == 0.495


def test_vehicle_charge_limit_caps_charge_to(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
) -> None:
    mapbox.serve(synthetic_directions(80, 60))
    api = wire(ScriptedStations())
    curve = [{"soc": 0.0, "power_kw": 50.0}, {"soc": 0.7, "power_kw": 50.0}]
    vehicle = add_vehicle(curve=curve)

    response = api.post(PLAN, json=synthetic_body(vehicle, 80), headers=auth())

    assert response.json()["assumptions"]["charge_to_soc"] == 0.7


def test_another_users_vehicle_is_404_and_nothing_is_stored(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    db: Session,
) -> None:
    api = wire(ScriptedStations())
    theirs = add_vehicle(user_id=OTHER_USER_ID)

    response = api.post(PLAN, json=body(theirs), headers=auth())
    unknown = api.post(PLAN, json={**body(theirs), "vehicle_id": str(uuid.uuid4())}, headers=auth())

    for r in (response, unknown):
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"
    assert mapbox.requests == []
    assert trip_count(db) == 0


def test_requires_authentication(wire: Callable[..., TestClient]) -> None:
    response = wire(ScriptedStations()).post(PLAN, json={})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Routes and upstream failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "code"),
    [(500, "MAPBOX_UNREACHABLE"), (401, "MAPBOX_NOT_CONFIGURED"), (422, "MAPBOX_ERROR")],
)
def test_mapbox_failure_is_an_error_envelope_and_stores_nothing(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    db: Session,
    status_code: int,
    code: str,
) -> None:
    mapbox.respond = lambda _r: httpx.Response(status_code, json={"code": "Down"})
    api = wire(ScriptedStations())
    vehicle = add_vehicle()

    response = api.post(PLAN, json=body(vehicle), headers=auth())

    assert response.json()["error"]["code"] == code
    assert response.status_code in (502, 503)
    assert trip_count(db) == 0


def test_mapbox_timeout_is_504(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    mapbox.respond = timeout
    api = wire(ScriptedStations())
    vehicle = add_vehicle()

    response = api.post(PLAN, json=body(vehicle), headers=auth())

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "MAPBOX_UNREACHABLE"


def test_afdc_down_is_stations_unreachable_and_stores_nothing(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    afdc: FakeAfdc,
    db: Session,
) -> None:
    afdc.fail = True
    api = wire()
    vehicle = add_vehicle()

    response = api.post(PLAN, json=body(vehicle), headers=auth())

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "STATIONS_UNREACHABLE"
    assert trip_count(db) == 0


def test_stations_provider_error_mid_plan_stores_nothing(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    db: Session,
) -> None:
    mapbox.serve(synthetic_directions(400, 270))
    api = wire(ScriptedStations(stations=trip2_stations(), fail=True))
    vehicle = add_vehicle()

    response = api.post(PLAN, json=synthetic_body(vehicle, 400), headers=auth())

    assert response.json()["error"]["code"] == "STATIONS_UNREACHABLE"
    assert trip_count(db) == 0


@pytest.mark.parametrize("fixture", ["directions_no_route.json", "directions_no_segment.json"])
def test_no_driving_route_is_a_stored_infeasible_plan(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
    fixture: str,
) -> None:
    mapbox.serve(_json(MAPBOX_FIXTURES / fixture))
    api = wire(scripted)
    vehicle = add_vehicle()

    response = api.post(PLAN, json=body(vehicle, route_index=2), headers=auth())

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["status"] == "infeasible"
    assert plan["route"] is None
    assert plan["reason"]["code"] == "NO_DRIVING_ROUTE"
    assert plan["reason"]["limiting_segment"] is None
    assert plan["stops"] == [] and plan["totals"] is None
    assert scripted.calls == []
    stored = api.get(f"/api/v1/trips/{plan['trip_id']}", headers=auth()).json()
    assert stored["plan_status"] == "infeasible"
    assert stored["plan"] == plan


def test_route_index_beyond_the_routes_is_422(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    db: Session,
) -> None:
    api = wire(ScriptedStations())
    vehicle = add_vehicle()

    response = api.post(PLAN, json=body(vehicle, route_index=3), headers=auth())

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "ROUTE_INDEX_UNAVAILABLE"
    assert trip_count(db) == 0


def test_route_index_selects_the_alternative(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
) -> None:
    fixture = _json(MAPBOX_FIXTURES / "directions_frederick_toronto.json")
    api = wire()
    vehicle = add_vehicle()

    plan = api.post(PLAN, json=body(vehicle, route_index=1), headers=auth()).json()

    assert plan["route"]["route_index"] == 1
    assert plan["route"]["geometry_polyline6"] == fixture["routes"][1]["geometry"]


# ---------------------------------------------------------------------------
# Candidates: exclusions, infeasibility, preferred networks
# ---------------------------------------------------------------------------


def test_exclusion_counts(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    mapbox.serve(synthetic_directions(400, 270))
    scripted.stations = [
        *trip2_stations(),
        synthetic_station("U", 210, 0.2, None),  # unknown power (D12)
        synthetic_station("FAR", 230, 5.0, 150),  # 2·5·1.3 km at 50 km/h = 15.6 min
    ]
    api = wire(scripted)
    vehicle = add_vehicle()

    response = api.post(
        PLAN,
        json=synthetic_body(vehicle, 400, preferences={"max_detour_minutes": 10}),
        headers=auth(),
    )

    candidates = response.json()["candidates"]
    assert candidates["corridor_km"] == pytest.approx(3.205, abs=1e-3)
    assert candidates == {
        "searched": True,
        "corridor_km": candidates["corridor_km"],
        "considered": 3,
        "excluded_incompatible": 1,
        "excluded_unknown_power": 1,
        "excluded_detour": 1,
    }


def test_no_usable_charger_is_infeasible_with_a_located_segment(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
    db: Session,
) -> None:
    """Only incompatible chargers nearby (PRD §9) → NO_COMPATIBLE_CHARGER_IN_RANGE."""

    mapbox.serve(synthetic_directions(400, 270))
    scripted.stations = [synthetic_station("X", 200, 0.3, 150, connector="CHAdeMO")]
    api = wire(scripted)
    vehicle = add_vehicle()

    response = api.post(
        PLAN, json=synthetic_body(vehicle, 400, starting_soc_override=0.8), headers=auth()
    )

    plan = response.json()
    assert plan["status"] == "infeasible"
    reason = plan["reason"]
    assert reason["code"] == "NO_COMPATIBLE_CHARGER_IN_RANGE"
    segment = reason["limiting_segment"]
    assert segment["from_km"] == 0
    # 0.80 - 0.15 = 0.65 SoC of driving at 0.0028169 SoC/km ≈ 230.75 km.
    assert segment["to_km"] == pytest.approx(230.75, abs=0.01)
    expected_lat, expected_lon = north_of(segment["to_km"])
    assert segment["from"] == {"lat": pytest.approx(39.0), "lon": pytest.approx(-77.0)}
    assert segment["to"]["lat"] == pytest.approx(expected_lat, abs=1e-4)
    assert segment["to"]["lon"] == pytest.approx(expected_lon, abs=1e-4)
    assert plan["candidates"]["excluded_incompatible"] == 1
    assert trip_count(db) == 1  # infeasible plans are stored too


def test_preferred_network_is_used_when_feasible(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    mapbox.serve(synthetic_directions(400, 270))
    # B (fastest) is Tesla; C is a slower EVgo station that also works.
    scripted.stations = [
        synthetic_station("B", 220, 1.0, 250, connector="NACS", network="Tesla"),
        synthetic_station("C", 225, 0.5, 100, network="EVGO"),
    ]
    api = wire(scripted)
    vehicle = add_vehicle()

    plan = api.post(
        PLAN,
        json=synthetic_body(
            vehicle, 400, starting_soc_override=0.8, preferences={"preferred_networks": ["EVgo"]}
        ),
        headers=auth(),
    ).json()

    assert [s["station"]["name"] for s in plan["stops"]] == ["C"]
    assert plan["assumptions"]["preferred_networks_relaxed"] is False


def test_preferred_networks_are_relaxed_when_infeasible(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    mapbox.serve(synthetic_directions(400, 270))
    scripted.stations = [
        # The only EVgo station is too early to finish the trip from 0.80.
        synthetic_station("L", 120, 0.2, 50, network="EVGO"),
        synthetic_station("B", 220, 1.0, 250, connector="NACS", network="Tesla"),
    ]
    api = wire(scripted)
    vehicle = add_vehicle()

    plan = api.post(
        PLAN,
        json=synthetic_body(
            vehicle,
            400,
            starting_soc_override=0.8,
            preferences={"preferred_networks": ["Electrify America", "EVgo"]},
        ),
        headers=auth(),
    ).json()

    assert plan["status"] == "feasible"
    assert [s["station"]["name"] for s in plan["stops"]] == ["B"]
    assert plan["assumptions"]["preferred_networks_relaxed"] is True


def test_plan_log_has_stage_timings_and_no_coordinates(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mapbox.serve(synthetic_directions(400, 270))
    scripted.stations = trip2_stations()
    api = wire(scripted)
    vehicle = add_vehicle()

    with caplog.at_level(logging.INFO, logger="app.modules.routing.planner_service"):
        api.post(PLAN, json=synthetic_body(vehicle, 400), headers=auth())

    [record] = [r for r in caplog.records if r.getMessage() == "route_plan"]
    fields = record.__dict__
    for stage in ("directions_ms", "optimizer_ms", "stations_ms", "persist_ms"):
        assert isinstance(fields[stage], float)
    assert fields["plan_status"] == "feasible"
    assert fields["stops"] == 1
    standard = logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None).__dict__
    extra = {k: v for k, v in fields.items() if k not in standard and k != "message"}
    assert "plan_status" in extra
    logged = json.dumps(extra, default=str)
    for needle in ("39.0", "-77.0", "lat", "lon", "geometry", "polyline"):
        assert needle not in logged


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_rounding_is_half_up() -> None:
    assert round_soc(0.1725) == 0.173
    assert round_soc(0.15296) == 0.153
    assert round_minutes(2.5) == 3
    assert round_minutes(3.12) == 3
    assert round_minutes(16.648) == 17


@pytest.mark.parametrize(
    ("minutes", "expected"), [(20, 6.41), (0, 0.5), (1, 0.5), (240, 16.0), (10, 3.21)]
)
def test_corridor_follows_detour_limit(minutes: int, expected: float) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert corridor_for(minutes, settings) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize(
    ("network", "preferred", "expected"),
    [
        ("Tesla", ["Tesla Supercharger"], True),
        ("Electrify America", ["electrify  america"], True),
        ("EVGO", ["EVgo"], True),
        ("ChargePoint Network", ["chargepoint"], True),
        ("EVGO", ["Tesla"], False),
        (None, ["EVgo"], False),
    ],
)
def test_preferred_network_matching(
    network: str | None, preferred: list[str], expected: bool
) -> None:
    from app.modules.stations.schemas import RouteStationOut
    from app.schemas.geo import Coordinates

    station = RouteStationOut(
        id=uuid.uuid4(),
        provider="afdc",
        external_id="1",
        name="S",
        operator=None,
        network=network,
        street_address=None,
        city=None,
        region=None,
        postal_code=None,
        country="US",
        location=Coordinates(lat=0, lon=0),
        connectors=[],
        max_power_kw=None,
        access="public",
        access_detail=None,
        restricted_access=False,
        status="open",
        pricing_text=None,
        compatible=None,
        source_updated_at=None,
        fetched_at=datetime.now(UTC),
        is_stale=False,
        distance_along_route_km=0,
        distance_from_route_km=0,
    )
    assert matches_preferred(station, preferred) is expected
