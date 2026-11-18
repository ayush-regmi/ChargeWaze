"""Fixtures for the stations API tests (Phase 3 F5).

AFDC is a real ``AfdcProvider`` over ``httpx.MockTransport`` serving the F0
fixtures, so every request goes through the real adapter. The database is the
shared Postgres test database (``tests/conftest.py``), rolled back per test.
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.geo import decode_polyline
from app.integrations.stations import AfdcProvider, get_station_provider
from app.modules.users.models import Profile
from app.modules.vehicles.models import Vehicle, VehicleSpecs
from tests.conftest import TEST_USER_ID, make_token

ROOT = Path(__file__).resolve().parents[4]
AFDC_FIXTURES = ROOT / "fixtures" / "stations" / "afdc"
MAPBOX_FIXTURES = ROOT / "fixtures" / "mapbox"
API_BASE = "https://afdc.test/api/alt-fuel-stations/v1"
API_KEY = "afdc-test-key-0123456789"
CURSOR_SECRET = "test-cursor-secret"
OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

Responder = Callable[[httpx.Request], httpx.Response]


def afdc_json(name: str) -> Any:
    return json.loads((AFDC_FIXTURES / name).read_bytes())


def route_a_polyline() -> str:
    """Real Mapbox route A, Frederick → Toronto via the QEW (the E2 route)."""

    body = json.loads((MAPBOX_FIXTURES / "directions_frederick_toronto.json").read_bytes())
    geometry: str = body["routes"][0]["geometry"]
    return geometry


def route_a_points() -> list[tuple[float, float]]:
    return decode_polyline(route_a_polyline())


def paged(body: dict[str, Any]) -> Responder:
    """Serve ``body``'s stations sliced by the request's ``offset``/``limit``."""

    def respond(request: httpx.Request) -> httpx.Response:
        params = request_params(request)
        offset = int(params.get("offset", "0"))
        limit = int(params.get("limit", "200"))
        page = copy.deepcopy(body)
        page["fuel_stations"] = body["fuel_stations"][offset : offset + limit]
        return httpx.Response(200, json=page)

    return respond


def request_params(request: httpx.Request) -> dict[str, str]:
    """Query string merged with the form body (AFDC POSTs use both)."""

    params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
    if request.method == "POST":
        params.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
    return params


@dataclass
class FakeAfdc:
    """Routes each AFDC path to a scripted responder and records requests."""

    nearest: Responder = field(
        default_factory=lambda: paged(afdc_json("nearest_frederick_dc_fast.json"))
    )
    nearby_route: Responder = field(
        default_factory=lambda: paged(afdc_json("nearby_route_cross_border_dc_fast.json"))
    )
    station: Responder = field(
        default_factory=lambda: (
            lambda _r: httpx.Response(200, json=afdc_json("station_448148.json"))
        )
    )
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/nearest.json"):
            return self.nearest(request)
        if path.endswith("/nearby-route.json"):
            return self.nearby_route(request)
        return self.station(request)

    def fail_all(self, responder: Responder) -> None:
        self.nearest = self.nearby_route = self.station = responder

    @property
    def params(self) -> list[dict[str, str]]:
        return [request_params(r) for r in self.requests]


@pytest.fixture()
def afdc() -> FakeAfdc:
    return FakeAfdc()


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        afdc_api_key=API_KEY,
        afdc_api_base_url=API_BASE,
        stations_cursor_secret=CURSOR_SECRET,
        stations_cache_ttl_hours=24,
    )


@pytest.fixture()
def provider(afdc: FakeAfdc, settings: Settings) -> Iterator[AfdcProvider]:
    p = AfdcProvider.from_settings(settings, transport=httpx.MockTransport(afdc))
    try:
        yield p
    finally:
        p.close()


@pytest.fixture()
def api(app_with_db: FastAPI, settings: Settings, provider: AfdcProvider) -> TestClient:
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    app_with_db.dependency_overrides[get_station_provider] = lambda: provider
    return TestClient(app_with_db)


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
    def _add(*, user_id: uuid.UUID = TEST_USER_ID, connectors: list[str]) -> Vehicle:
        if db.get(Profile, user_id) is None:
            db.add(Profile(id=user_id))
            db.flush()
        vehicle = Vehicle(
            user_id=user_id,
            smartcar_vehicle_id=f"sc-vehicle-{uuid.uuid4()}",
            smartcar_user_id="sc-user",
            make="Toyota",
            model="bZ XLE",
            year=2026,
            connection_status="connected",
            connected_at=datetime.now(UTC),
            supported_signals=[],
        )
        vehicle.specs = VehicleSpecs(
            usable_capacity_kwh=Decimal("71.40"), connectors=connectors, charging_curve=[]
        )
        db.add(vehicle)
        db.commit()
        return vehicle

    return _add
