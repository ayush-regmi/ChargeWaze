"""Fixtures for the P6-F5 account deletion, consent and retention tests.

Smartcar and the Supabase admin API are real adapters over
``httpx.MockTransport``, so nothing leaves the machine. ``seed_account``
gives a user one row in every user-owned table.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.integrations.smartcar.client import SmartcarClient, get_smartcar_client
from app.integrations.supabase.admin import SupabaseAdminClient, get_supabase_admin
from app.modules.stations.favorites_models import FavoriteStation
from app.modules.stations.models import ChargingStation
from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.trips.service import NewPlannedStop, TripEndpoint, create_planned_trip
from app.modules.users.models import Profile, UserPreferences
from app.modules.users.privacy_models import ConsentRecord
from app.modules.vehicles.models import Vehicle, VehicleRefresh, VehicleSpecs, VehicleTelemetry
from tests.conftest import TEST_USER_ID, make_token
from tests.integrations.conftest import TEST_MANAGEMENT_TOKEN, fixture_json

OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SUPABASE_URL = "https://supabase.test"
SERVICE_ROLE_KEY = "test-service-role-key"
BASE_TIME = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)


@dataclass
class FakeSmartcar:
    """Answers the token request and ``DELETE /connections/{id}``."""

    delete_status: int = 204
    transport_error: bool = False
    # Per connection ID overrides of ``delete_status``.
    status_for: dict[str, int] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json=fixture_json("token_response.json"))
        if request.method == "DELETE" and "/connections/" in request.url.path:
            if self.transport_error:
                raise httpx.ConnectError("boom", request=request)
            connection_id = request.url.path.rsplit("/", 1)[-1]
            status = self.status_for.get(connection_id, self.delete_status)
            if status == 204:
                return httpx.Response(204)
            return httpx.Response(status, json={"errors": []})
        return httpx.Response(404, json={"errors": []})

    @property
    def deleted_connections(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.requests if r.method == "DELETE"]


@dataclass
class FakeSupabase:
    status: int = 200
    transport_error: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.transport_error:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(self.status, json={})

    @property
    def deleted_users(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.requests if r.method == "DELETE"]


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        smartcar_client_id="test-client-id",
        smartcar_client_secret="test-client-secret",
        smartcar_api_base_url="https://vehicle.test/v3",
        smartcar_auth_url="https://iam.test/oauth2/token",
        smartcar_management_token=TEST_MANAGEMENT_TOKEN,
        supabase_url=SUPABASE_URL,
        supabase_service_role_key=SERVICE_ROLE_KEY,
    )


@pytest.fixture()
def smartcar() -> FakeSmartcar:
    return FakeSmartcar()


@pytest.fixture()
def supabase() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture()
def smartcar_client(settings: Settings, smartcar: FakeSmartcar) -> Iterator[SmartcarClient]:
    client = SmartcarClient.from_settings(settings, transport=httpx.MockTransport(smartcar.handler))
    yield client
    client.close()


@pytest.fixture()
def supabase_client(settings: Settings, supabase: FakeSupabase) -> Iterator[SupabaseAdminClient]:
    client = SupabaseAdminClient.from_settings(
        settings, transport=httpx.MockTransport(supabase.handler)
    )
    yield client
    client.close()


@pytest.fixture()
def api(
    app_with_db: FastAPI,
    settings: Settings,
    smartcar_client: SmartcarClient,
    supabase_client: SupabaseAdminClient,
) -> TestClient:
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    app_with_db.dependency_overrides[get_smartcar_client] = lambda: smartcar_client
    app_with_db.dependency_overrides[get_supabase_admin] = lambda: supabase_client
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


@dataclass(frozen=True)
class SeededAccount:
    user_id: uuid.UUID
    vehicle_id: uuid.UUID
    simulated_vehicle_id: uuid.UUID
    smartcar_vehicle_id: str
    smartcar_user_id: str
    connection_id: str
    station_id: uuid.UUID
    trip_id: uuid.UUID
    leg_id: uuid.UUID


def _station(db: Session, name: str) -> ChargingStation:
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
    db.flush()
    return station


@pytest.fixture()
def seed_account(db: Session) -> Callable[..., SeededAccount]:
    """One row in every user-owned table for ``user_id``. Commits."""

    def _seed(user_id: uuid.UUID = TEST_USER_ID, *, tag: str = "a") -> SeededAccount:
        connection_id = f"sc-connection-{tag}"
        smartcar_vehicle_id = f"sc-vehicle-{tag}"
        smartcar_user_id = f"sc-user-{tag}"
        db.add(Profile(id=user_id, display_name=f"Driver {tag}"))
        db.flush()

        vehicle = Vehicle(
            user_id=user_id,
            smartcar_vehicle_id=smartcar_vehicle_id,
            smartcar_user_id=smartcar_user_id,
            smartcar_connection_id=connection_id,
            make="Toyota",
            model="bZ XLE AWD",
            year=2026,
            connection_status="connected",
            connected_at=BASE_TIME,
            supported_signals=["soc", "odometer"],
            latest_state=None,
        )
        # P4-F8 per-vehicle baseline lives on the specs row.
        vehicle.specs = VehicleSpecs(
            usable_capacity_kwh=Decimal("71.40"),
            connectors=["NACS"],
            charging_curve=[],
            baseline_kwh_per_km=Decimal("0.180"),
        )
        simulated = Vehicle(
            user_id=user_id,
            make="Tesla",
            model="Model 3",
            year=2024,
            connection_status="connected",
            connected_at=BASE_TIME,
            supported_signals=[],
            is_simulated=True,
        )
        db.add_all([vehicle, simulated])
        db.flush()

        db.add(
            UserPreferences(
                user_id=user_id, preferred_networks=["EVgo"], primary_vehicle_id=vehicle.id
            )
        )
        db.add(
            VehicleTelemetry(
                vehicle_id=vehicle.id,
                recorded_at=BASE_TIME,
                source="smartcar",
                source_event_id=f"evt-{tag}",
                signal_code="soc",
                value=Decimal("72"),
                unit="percent",
                raw_payload={},
            )
        )
        db.add(VehicleRefresh(vehicle_id=vehicle.id, requested_at=BASE_TIME, outcome="ok"))

        station = _station(db, f"Station {tag}")
        db.add(FavoriteStation(user_id=user_id, station_id=station.id))

        trip_id = uuid.uuid4()
        create_planned_trip(
            db,
            trip_id=trip_id,
            created_at=BASE_TIME,
            user_id=user_id,
            vehicle_id=vehicle.id,
            origin=TripEndpoint(lat=39.41, lon=-77.41, label="A"),
            destination=TripEndpoint(lat=43.65, lon=-79.38, label="B"),
            departure_time=None,
            plan_status="feasible",
            plan_payload={"status": "feasible", "trip_id": str(trip_id)},
            model_version="deterministic-v1",
            stops=[
                NewPlannedStop(
                    sequence=1,
                    station_id=station.id,
                    arrival_soc=0.2,
                    departure_soc=0.8,
                    charging_minutes=25,
                )
            ],
        )

        leg = ObservedTrip(
            user_id=user_id,
            vehicle_id=vehicle.id,
            start_time=BASE_TIME,
            start_time_latest=BASE_TIME,
            end_time=BASE_TIME + timedelta(minutes=50),
            odometer_start_km=Decimal("24650.00"),
            odometer_end_km=Decimal("24712.40"),
            distance_km=Decimal("62.40"),
            soc_start_percent=Decimal("80"),
            soc_start_recorded_at=BASE_TIME,
            soc_end_percent=Decimal("70"),
            soc_end_recorded_at=BASE_TIME + timedelta(minutes=50),
            usable_capacity_kwh=Decimal("71.00"),
            capacity_source="catalog",
            energy_kwh=Decimal("7.100"),
            energy_kwh_uncertainty=Decimal("0.710"),
            energy_source="soc_inferred",
            quality_flag="high",
            review_status="auto",
            closed=True,
            inference_version="telemetry-v1",
            planned_trip_id=trip_id,
        )
        db.add(leg)
        db.flush()
        prediction_common: dict[str, Any] = {
            "user_id": user_id,
            "vehicle_id": vehicle.id,
            "model_version": "deterministic-v1",
            "distance_km": Decimal("62.40"),
            "observed_distance_km": Decimal("62.40"),
            "predicted_energy_kwh": Decimal("10.920"),
            "observed_energy_kwh": Decimal("7.100"),
            "error_kwh": Decimal("-3.820"),
        }
        db.add(
            PredictionResult(
                kind="leg",
                observed_trip_id=leg.id,
                inputs={
                    "baseline_kwh_per_km": 0.18,
                    "baseline_source": "vehicle",
                    "usable_capacity_kwh": 71.0,
                },
                **prediction_common,
            )
        )
        db.add(
            PredictionResult(
                kind="plan",
                planned_trip_id=trip_id,
                inputs={"plan_model_version": "deterministic-v1"},
                **prediction_common,
            )
        )
        db.add(
            ConsentRecord(
                user_id=user_id,
                kind="privacy_notice",
                version="2026-11-19",
                granted=True,
                source="web",
            )
        )
        seeded = SeededAccount(
            user_id=user_id,
            vehicle_id=vehicle.id,
            simulated_vehicle_id=simulated.id,
            smartcar_vehicle_id=smartcar_vehicle_id,
            smartcar_user_id=smartcar_user_id,
            connection_id=connection_id,
            station_id=station.id,
            trip_id=trip_id,
            leg_id=leg.id,
        )
        # Read the IDs before committing: touching expired attributes after
        # the commit would open a savepoint that predates the request under
        # test, and a later rollback in this session would undo that request.
        db.commit()
        return seeded

    return _seed
