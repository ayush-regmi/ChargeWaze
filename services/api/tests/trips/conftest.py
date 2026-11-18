"""Fixtures for the trips tests (Phase 4 F1).

Trips are written through ``service.create_planned_trip``, the same function
the planner (F4) calls. The database is the shared Postgres test database
(``tests/conftest.py``), rolled back per test.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection
from sqlalchemy.orm import Session

from app.modules.stations.models import ChargingStation
from app.modules.trips.service import NewPlannedStop, TripEndpoint, create_planned_trip
from app.modules.users.models import Profile
from app.modules.vehicles.models import Vehicle
from tests.conftest import TEST_USER_ID, make_token

OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
BASE_TIME = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)

ORIGIN = TripEndpoint(lat=39.4143, lon=-77.4105, label="Frederick, MD")
DESTINATION = TripEndpoint(lat=43.6532, lon=-79.3832, label="Toronto, ON")

# A trimmed §3.7 feasible body; enough for the list fields.
FEASIBLE_PAYLOAD: dict[str, Any] = {
    "status": "feasible",
    "model_version": "deterministic-v1",
    "origin": {"lat": 39.4143, "lon": -77.4105, "label": "Frederick, MD"},
    "destination": {"lat": 43.6532, "lon": -79.3832, "label": "Toronto, ON"},
    "route": {"route_index": 0, "distance_km": 748.1, "drive_minutes": 515},
    "stops": [],
    "arrival_soc": 0.15,
    "totals": {"total_minutes": 583, "charging_minutes": 45, "energy_kwh_estimated": 150.92},
    "alternatives": [],
    "reason": None,
}

INFEASIBLE_PAYLOAD: dict[str, Any] = {
    "status": "infeasible",
    "route": {"route_index": 0, "distance_km": 748.1, "drive_minutes": 515},
    "stops": [],
    "arrival_soc": None,
    "totals": None,
    "reason": {"code": "DESTINATION_UNREACHABLE", "message": "gap", "limiting_segment": None},
}

NO_ROUTE_PAYLOAD: dict[str, Any] = {
    "status": "infeasible",
    "route": None,
    "stops": [],
    "arrival_soc": None,
    "totals": None,
    "reason": {"code": "NO_DRIVING_ROUTE", "message": "none", "limiting_segment": None},
}


@pytest.fixture()
def db(db_connection: Connection) -> Iterator[Session]:
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def api(app_with_db: FastAPI) -> TestClient:
    return TestClient(app_with_db)


@pytest.fixture()
def auth(key_material: Any) -> Callable[..., dict[str, str]]:
    def _auth(user_id: uuid.UUID = TEST_USER_ID) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(key_material, sub=user_id)}"}

    return _auth


@pytest.fixture()
def add_vehicle(db: Session) -> Callable[..., Vehicle]:
    def _add(*, user_id: uuid.UUID = TEST_USER_ID) -> Vehicle:
        if db.get(Profile, user_id) is None:
            db.add(Profile(id=user_id))
            db.flush()
        vehicle = Vehicle(
            user_id=user_id,
            smartcar_vehicle_id=f"sc-vehicle-{uuid.uuid4()}",
            smartcar_user_id="sc-user",
            make="Toyota",
            model="bZ XLE AWD",
            year=2026,
            connection_status="connected",
            connected_at=BASE_TIME,
            supported_signals=[],
        )
        db.add(vehicle)
        db.commit()
        return vehicle

    return _add


@pytest.fixture()
def add_station(db: Session) -> Callable[..., ChargingStation]:
    def _add(name: str = "Example station") -> ChargingStation:
        station = ChargingStation(
            provider="afdc",
            external_id=str(uuid.uuid4()),
            name=name,
            lat=Decimal("40.273200"),
            lon=Decimal("-76.886700"),
            connectors=[{"type": "CCS1", "count": 2, "power_kw": 150}],
            raw_payload={},
        )
        db.add(station)
        db.commit()
        return station

    return _add


@pytest.fixture()
def add_trip(db: Session) -> Callable[..., uuid.UUID]:
    """Store a plan through ``create_planned_trip`` and optionally save it."""

    def _add(
        vehicle: Vehicle,
        *,
        minutes: int = 0,
        saved_minutes: int | None = None,
        plan_status: str = "feasible",
        payload: dict[str, Any] | None = None,
        stops: Sequence[NewPlannedStop] = (),
        trip_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        tid = trip_id or uuid.uuid4()
        body = copy.deepcopy(payload if payload is not None else FEASIBLE_PAYLOAD)
        body["trip_id"] = str(tid)
        create_planned_trip(
            db,
            trip_id=tid,
            created_at=BASE_TIME + timedelta(minutes=minutes),
            user_id=vehicle.user_id,
            vehicle_id=vehicle.id,
            origin=ORIGIN,
            destination=DESTINATION,
            departure_time=None,
            plan_status=plan_status,  # type: ignore[arg-type]
            plan_payload=body,
            model_version="deterministic-v1",
            stops=stops,
        )
        if saved_minutes is not None:
            from app.modules.trips.models import PlannedTrip

            trip = db.get(PlannedTrip, tid)
            assert trip is not None
            trip.saved_at = BASE_TIME + timedelta(minutes=saved_minutes)
        db.commit()
        return tid

    return _add
