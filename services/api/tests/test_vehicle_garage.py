"""Tests for the Garage endpoints (Phase 2 F4).

``GET /api/v1/vehicles``, ``GET /api/v1/vehicles/{id}`` and
``DELETE /api/v1/vehicles/{id}``. Smartcar is a real ``SmartcarClient`` over
``httpx.MockTransport``, so the revoke call goes through the real adapter.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
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
from app.modules.users.models import Profile
from app.modules.vehicles.models import Vehicle, VehicleSpecs
from tests.conftest import TEST_USER_ID, make_token
from tests.integrations.conftest import fixture_json

VEHICLES = "/api/v1/vehicles"
OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SC_CONNECTION_ID = "sc-connection-garage"


@dataclass
class FakeSmartcar:
    delete_status: int = 204
    transport_error: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json=fixture_json("token_response.json"))
        if request.method == "DELETE" and "/connections/" in request.url.path:
            if self.transport_error:
                raise httpx.ConnectError("boom", request=request)
            if self.delete_status == 204:
                return httpx.Response(204)
            return httpx.Response(self.delete_status, json={"errors": []})
        return httpx.Response(404, json={"errors": []})

    @property
    def deletes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "DELETE"]


@pytest.fixture()
def smartcar() -> FakeSmartcar:
    return FakeSmartcar()


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        smartcar_client_id="test-client-id",
        smartcar_client_secret="test-client-secret",
        smartcar_api_base_url="https://vehicle.test/v3",
        smartcar_auth_url="https://iam.test/oauth2/token",
        telemetry_stale_after_minutes=15,
    )


@pytest.fixture()
def api(app_with_db: FastAPI, settings: Settings, smartcar: FakeSmartcar) -> Iterator[TestClient]:
    sc_client = SmartcarClient.from_settings(
        settings, transport=httpx.MockTransport(smartcar.handler)
    )
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    app_with_db.dependency_overrides[get_smartcar_client] = lambda: sc_client
    try:
        yield TestClient(app_with_db)
    finally:
        sc_client.close()


@pytest.fixture()
def db(db_connection: Connection) -> Iterator[Session]:
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _auth(key_material: Any, user_id: uuid.UUID = TEST_USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(key_material, sub=user_id)}"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _add_vehicle(
    db: Session,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    status: str = "connected",
    connected_at: datetime | None = None,
    latest_state: dict[str, Any] | None = None,
    connection_id: str | None = SC_CONNECTION_ID,
    with_specs: bool = True,
    model: str = "bZ XLE",
) -> Vehicle:
    if db.get(Profile, user_id) is None:
        db.add(Profile(id=user_id))
        db.flush()
    vehicle = Vehicle(
        user_id=user_id,
        smartcar_vehicle_id=f"sc-vehicle-{uuid.uuid4()}",
        smartcar_user_id="sc-user",
        smartcar_connection_id=connection_id,
        make="Toyota",
        model=model,
        year=2026,
        connection_status=status,
        connected_at=connected_at or datetime.now(UTC),
        disconnected_at=datetime.now(UTC) if status == "disconnected" else None,
        supported_signals=["soc", "range", "odometer", "charging"],
        latest_state=latest_state,
    )
    if with_specs:
        vehicle.specs = VehicleSpecs(
            usable_capacity_kwh=Decimal("71.40"),
            connectors=["NACS"],
            charging_curve=[{"soc": 0.1, "power_kw": 150}, {"soc": 0.8, "power_kw": 60}],
        )
    db.add(vehicle)
    db.flush()
    db.commit()
    return vehicle


def _reload(db: Session, vehicle: Vehicle) -> Vehicle:
    db.expire_all()
    fresh = db.get(Vehicle, vehicle.id)
    assert fresh is not None
    return fresh


# ---------------------------------------------------------------------------
# GET /api/v1/vehicles
# ---------------------------------------------------------------------------


def test_list_requires_auth(api: TestClient) -> None:
    response = api.get(VEHICLES)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_list_empty_for_new_user(api: TestClient, key_material: Any) -> None:
    response = api.get(VEHICLES, headers=_auth(key_material))
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_list_returns_only_callers_connected_vehicles(
    api: TestClient, db: Session, key_material: Any
) -> None:
    mine = _add_vehicle(db)
    _add_vehicle(db, status="disconnected")
    _add_vehicle(db, user_id=OTHER_USER_ID)

    response = api.get(VEHICLES, headers=_auth(key_material))
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [str(mine.id)]
    item = items[0]
    assert item["make"] == "Toyota"
    assert item["model"] == "bZ XLE"
    assert item["year"] == 2026
    assert item["connection_status"] == "connected"
    assert item["supported_signals"] == ["soc", "range", "odometer", "charging"]


def test_list_include_disconnected(api: TestClient, db: Session, key_material: Any) -> None:
    _add_vehicle(db)
    gone = _add_vehicle(db, status="disconnected")
    _add_vehicle(db, user_id=OTHER_USER_ID, status="disconnected")

    response = api.get(
        VEHICLES, params={"include_disconnected": "true"}, headers=_auth(key_material)
    )
    items = response.json()["items"]
    assert len(items) == 2
    assert {i["connection_status"] for i in items} == {"connected", "disconnected"}
    assert str(gone.id) in {i["id"] for i in items}


def test_list_orders_most_recently_connected_first(
    api: TestClient, db: Session, key_material: Any
) -> None:
    now = datetime.now(UTC)
    older = _add_vehicle(db, connected_at=now - timedelta(days=2))
    newer = _add_vehicle(db, connected_at=now - timedelta(hours=1))

    items = api.get(VEHICLES, headers=_auth(key_material)).json()["items"]
    assert [i["id"] for i in items] == [str(newer.id), str(older.id)]


def test_list_no_telemetry_is_stale_with_null_timestamp(
    api: TestClient, db: Session, key_material: Any
) -> None:
    _add_vehicle(db, latest_state=None)
    item = api.get(VEHICLES, headers=_auth(key_material)).json()["items"][0]
    assert item["last_state_recorded_at"] is None
    assert item["is_stale"] is True


def test_list_fresh_and_stale_state(api: TestClient, db: Session, key_material: Any) -> None:
    now = datetime.now(UTC)
    fresh_at = now - timedelta(minutes=2)
    stale_at = now - timedelta(minutes=40)
    fresh = _add_vehicle(
        db,
        connected_at=now,
        latest_state={
            "recorded_at": _iso(fresh_at),
            "signals": {"soc": {"value": 72, "unit": "percent", "recorded_at": _iso(fresh_at)}},
        },
    )
    stale = _add_vehicle(
        db,
        connected_at=now - timedelta(days=1),
        latest_state={
            "recorded_at": _iso(stale_at),
            "signals": {"soc": {"value": 50, "unit": "percent", "recorded_at": _iso(stale_at)}},
        },
    )

    items = {i["id"]: i for i in api.get(VEHICLES, headers=_auth(key_material)).json()["items"]}
    assert items[str(fresh.id)]["is_stale"] is False
    assert datetime.fromisoformat(items[str(fresh.id)]["last_state_recorded_at"]) == fresh_at
    assert items[str(stale.id)]["is_stale"] is True
    assert datetime.fromisoformat(items[str(stale.id)]["last_state_recorded_at"]) == stale_at


def test_list_uses_newest_per_signal_timestamp(
    api: TestClient, db: Session, key_material: Any
) -> None:
    """D5: the top-level time is the max across signals, even if the top key is missing."""

    now = datetime.now(UTC)
    newest = now - timedelta(minutes=1)
    _add_vehicle(
        db,
        latest_state={
            "signals": {
                "soc": {"value": 72, "recorded_at": _iso(now - timedelta(hours=3))},
                "range": {"value": 290, "recorded_at": _iso(newest)},
            }
        },
    )
    item = api.get(VEHICLES, headers=_auth(key_material)).json()["items"][0]
    assert datetime.fromisoformat(item["last_state_recorded_at"]) == newest
    assert item["is_stale"] is False


def test_list_malformed_state_is_treated_as_no_data(
    api: TestClient, db: Session, key_material: Any
) -> None:
    _add_vehicle(db, latest_state={"recorded_at": "not-a-date", "signals": ["oops"]})
    item = api.get(VEHICLES, headers=_auth(key_material)).json()["items"][0]
    assert item["last_state_recorded_at"] is None
    assert item["is_stale"] is True


def test_list_never_calls_smartcar(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    _add_vehicle(db)
    api.get(VEHICLES, headers=_auth(key_material))
    assert smartcar.requests == []


# ---------------------------------------------------------------------------
# GET /api/v1/vehicles/{id}
# ---------------------------------------------------------------------------


def test_detail_returns_vehicle_with_specs(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    vehicle = _add_vehicle(db)
    response = api.get(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(vehicle.id)
    assert body["connection_status"] == "connected"
    assert body["connected_at"] is not None
    assert body["disconnected_at"] is None
    assert body["specs"] == {
        "usable_capacity_kwh": 71.4,
        "connectors": ["NACS"],
        "charging_curve": [{"soc": 0.1, "power_kw": 150.0}, {"soc": 0.8, "power_kw": 60.0}],
    }
    assert body["is_stale"] is True
    assert smartcar.requests == []


def test_detail_null_capacity_and_missing_specs(
    api: TestClient, db: Session, key_material: Any
) -> None:
    no_specs = _add_vehicle(db, with_specs=False)
    body = api.get(f"{VEHICLES}/{no_specs.id}", headers=_auth(key_material)).json()
    assert body["specs"] is None

    unknown = _add_vehicle(db, with_specs=False)
    unknown.specs = VehicleSpecs(usable_capacity_kwh=None, connectors=[], charging_curve=[])
    db.commit()
    body = api.get(f"{VEHICLES}/{unknown.id}", headers=_auth(key_material)).json()
    assert body["specs"] == {"usable_capacity_kwh": None, "connectors": [], "charging_curve": []}


def test_detail_of_disconnected_vehicle_is_readable(
    api: TestClient, db: Session, key_material: Any
) -> None:
    vehicle = _add_vehicle(db, status="disconnected")
    body = api.get(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material)).json()
    assert body["connection_status"] == "disconnected"
    assert body["disconnected_at"] is not None


def test_detail_other_users_vehicle_is_404(api: TestClient, db: Session, key_material: Any) -> None:
    theirs = _add_vehicle(db, user_id=OTHER_USER_ID)
    response = api.get(f"{VEHICLES}/{theirs.id}", headers=_auth(key_material))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_detail_unknown_id_is_404(api: TestClient, key_material: Any) -> None:
    response = api.get(f"{VEHICLES}/{uuid.uuid4()}", headers=_auth(key_material))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_detail_non_uuid_is_validation_error(api: TestClient, key_material: Any) -> None:
    response = api.get(f"{VEHICLES}/not-a-uuid", headers=_auth(key_material))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_detail_requires_auth(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    assert api.get(f"{VEHICLES}/{vehicle.id}").status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/vehicles/{id}
# ---------------------------------------------------------------------------


def test_disconnect_revokes_and_hides_from_list(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    vehicle = _add_vehicle(db)
    response = api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material))
    assert response.status_code == 204
    assert response.content == b""

    assert len(smartcar.deletes) == 1
    assert smartcar.deletes[0].url.path == f"/v3/connections/{SC_CONNECTION_ID}"

    fresh = _reload(db, vehicle)
    assert fresh.connection_status == "disconnected"
    assert fresh.disconnected_at is not None
    assert fresh.smartcar_connection_id is None  # nothing left to revoke
    assert api.get(VEHICLES, headers=_auth(key_material)).json() == {"items": []}


def test_disconnect_keeps_row_and_specs(api: TestClient, db: Session, key_material: Any) -> None:
    vehicle = _add_vehicle(db)
    api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material))
    fresh = _reload(db, vehicle)
    assert fresh.specs is not None
    assert fresh.smartcar_vehicle_id is not None  # needed for D4 reconnect


@pytest.mark.parametrize("delete_status", [500, 401])
def test_disconnect_smartcar_error_still_disconnects_locally(
    api: TestClient,
    db: Session,
    key_material: Any,
    smartcar: FakeSmartcar,
    delete_status: int,
) -> None:
    smartcar.delete_status = delete_status
    vehicle = _add_vehicle(db)
    response = api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material))
    assert response.status_code == 204

    fresh = _reload(db, vehicle)
    assert fresh.connection_status == "disconnected"
    # Revocation still owed: the connection ID stays for a retry job.
    assert fresh.smartcar_connection_id == SC_CONNECTION_ID


def test_disconnect_smartcar_unreachable_still_disconnects_locally(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    smartcar.transport_error = True
    vehicle = _add_vehicle(db)
    assert api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material)).status_code == 204
    fresh = _reload(db, vehicle)
    assert fresh.connection_status == "disconnected"
    assert fresh.smartcar_connection_id == SC_CONNECTION_ID


def test_disconnect_smartcar_not_configured_still_disconnects_locally(
    app_with_db: FastAPI, db: Session, key_material: Any
) -> None:
    unconfigured = Settings(_env_file=None, app_env="test")  # type: ignore[call-arg]
    sc_client = SmartcarClient.from_settings(
        unconfigured, transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    app_with_db.dependency_overrides[get_settings] = lambda: unconfigured
    app_with_db.dependency_overrides[get_smartcar_client] = lambda: sc_client
    vehicle = _add_vehicle(db)
    response = TestClient(app_with_db).delete(
        f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material)
    )
    sc_client.close()
    assert response.status_code == 204
    assert _reload(db, vehicle).connection_status == "disconnected"


def test_disconnect_connection_already_gone_counts_as_revoked(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    smartcar.delete_status = 404
    vehicle = _add_vehicle(db)
    assert api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material)).status_code == 204
    assert _reload(db, vehicle).smartcar_connection_id is None


def test_disconnect_without_connection_id_skips_smartcar(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    vehicle = _add_vehicle(db, connection_id=None)
    assert api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material)).status_code == 204
    assert smartcar.requests == []
    assert _reload(db, vehicle).connection_status == "disconnected"


def test_disconnect_is_idempotent(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    vehicle = _add_vehicle(db)
    assert api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material)).status_code == 204
    first_at = _reload(db, vehicle).disconnected_at

    assert api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material)).status_code == 204
    assert len(smartcar.deletes) == 1
    assert _reload(db, vehicle).disconnected_at == first_at


def test_disconnect_other_users_vehicle_is_404_and_untouched(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    theirs = _add_vehicle(db, user_id=OTHER_USER_ID)
    response = api.delete(f"{VEHICLES}/{theirs.id}", headers=_auth(key_material))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert smartcar.requests == []
    assert _reload(db, theirs).connection_status == "connected"


def test_disconnect_unknown_id_is_404(api: TestClient, key_material: Any) -> None:
    response = api.delete(f"{VEHICLES}/{uuid.uuid4()}", headers=_auth(key_material))
    assert response.status_code == 404


def test_disconnect_requires_auth(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    assert api.delete(f"{VEHICLES}/{vehicle.id}").status_code == 401
    assert _reload(db, vehicle).connection_status == "connected"


def test_disconnect_logs_no_smartcar_ids(
    api: TestClient,
    db: Session,
    key_material: Any,
    smartcar: FakeSmartcar,
    caplog: pytest.LogCaptureFixture,
) -> None:
    smartcar.delete_status = 500
    vehicle = _add_vehicle(db)
    with caplog.at_level(logging.DEBUG):
        api.delete(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material))
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert SC_CONNECTION_ID not in text
    assert vehicle.smartcar_vehicle_id is not None
    assert vehicle.smartcar_vehicle_id not in text
