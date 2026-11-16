"""Tests for ``POST /api/v1/webhooks/smartcar`` (Phase 2 F5).

Covers the roadmap's required cases (invalid signature, duplicate, out-of-order,
stale data lives in F4/F6) plus the F5 plan list. Payloads come from
``fixtures/smartcar/``; signatures use the documented test-only management
token. Everything runs against the migrated Postgres test database.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_engine
from app.integrations.smartcar.schemas import VehicleStateData, normalize_signals
from app.integrations.smartcar.signatures import compute_signature
from app.modules.users.models import Profile
from app.modules.vehicles import webhook_router
from app.modules.vehicles.models import Vehicle, VehicleTelemetry
from app.modules.vehicles.telemetry_service import apply_to_latest_state
from tests.conftest import TEST_USER_ID
from tests.integrations.conftest import TEST_MANAGEMENT_TOKEN, fixture_bytes, fixture_json

WEBHOOK = "/api/v1/webhooks/smartcar"
SC_USER = "redacted-id-1"
SC_VEHICLE = "redacted-id-3"

# Fixture timestamps (ms): evt-0001 SoC @ ...600000, evt-0002 @ ...660000, evt-0003 @ ...780000.
T_EVT1 = datetime(2026, 11, 9, 22, 0, 0, tzinfo=UTC)
T_EVT2 = datetime(2026, 11, 9, 22, 1, 0, tzinfo=UTC)
T_EVT3 = datetime(2026, 11, 9, 22, 3, 0, tzinfo=UTC)


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        smartcar_management_token=TEST_MANAGEMENT_TOKEN,
    )


@pytest.fixture()
def api(app_with_db: FastAPI, settings: Settings) -> Iterator[TestClient]:
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app_with_db)


@pytest.fixture()
def db(db_connection: Connection) -> Iterator[Session]:
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _add_vehicle(
    db: Session,
    *,
    status: str = "connected",
    smartcar_vehicle_id: str = SC_VEHICLE,
    smartcar_user_id: str = SC_USER,
    supported_signals: list[str] | None = None,
    latest_state: dict[str, Any] | None = None,
) -> Vehicle:
    if db.get(Profile, TEST_USER_ID) is None:
        db.add(Profile(id=TEST_USER_ID))
        db.flush()
    vehicle = Vehicle(
        user_id=TEST_USER_ID,
        smartcar_vehicle_id=smartcar_vehicle_id,
        smartcar_user_id=smartcar_user_id,
        make="Toyota",
        model="bZ XLE",
        year=2026,
        connection_status=status,
        connected_at=datetime.now(UTC),
        disconnected_at=datetime.now(UTC) if status == "disconnected" else None,
        supported_signals=supported_signals if supported_signals is not None else [],
        latest_state=latest_state,
    )
    db.add(vehicle)
    db.commit()
    return vehicle


def _post(api: TestClient, body: bytes, *, signature: str | None = "auto") -> Any:
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers["SC-Signature"] = compute_signature(body, TEST_MANAGEMENT_TOKEN)
    elif signature is not None:
        headers["SC-Signature"] = signature
    return api.post(WEBHOOK, content=body, headers=headers)


def _post_fixture(api: TestClient, name: str) -> Any:
    body = fixture_bytes(name)
    signature = fixture_bytes(f"{name}.signature").decode().strip() if name in _SIGNED else "auto"
    return _post(api, body, signature=signature)


_SIGNED = {"webhook_vehicle_state.json", "webhook_verify_challenge.json"}


def _encode(payload: Any) -> bytes:
    return json.dumps(payload).encode()


def _telemetry(db: Session, vehicle: Vehicle | None = None) -> list[VehicleTelemetry]:
    db.expire_all()
    stmt = select(VehicleTelemetry).order_by(
        VehicleTelemetry.recorded_at, VehicleTelemetry.signal_code
    )
    if vehicle is not None:
        stmt = stmt.where(VehicleTelemetry.vehicle_id == vehicle.id)
    return list(db.scalars(stmt))


def _count(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(VehicleTelemetry)) or 0


def _reload(db: Session, vehicle: Vehicle) -> Vehicle:
    db.expire_all()
    fresh = db.get(Vehicle, vehicle.id)
    assert fresh is not None
    return fresh


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Signature (step 1)
# ---------------------------------------------------------------------------


def test_invalid_signature_rejected_and_nothing_written(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    response = _post(api, fixture_bytes("webhook_vehicle_state.json"), signature="0" * 64)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"
    assert _count(db) == 0
    assert _reload(db, vehicle).latest_state is None


def test_missing_signature_rejected(api: TestClient, db: Session) -> None:
    _add_vehicle(db)
    response = _post(api, fixture_bytes("webhook_vehicle_state.json"), signature=None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"
    assert _count(db) == 0


def test_body_tampered_after_signing_rejected(api: TestClient, db: Session) -> None:
    _add_vehicle(db)
    signature = fixture_bytes("webhook_vehicle_state.json.signature").decode().strip()
    tampered = fixture_bytes("webhook_vehicle_state.json").replace(b'"value": 64', b'"value": 99')
    response = _post(api, tampered, signature=signature)
    assert response.status_code == 401
    assert _count(db) == 0


def test_unset_management_token_rejects_everything(app_with_db: FastAPI, db: Session) -> None:
    unset = Settings(_env_file=None, app_env="test", smartcar_management_token="")  # type: ignore[call-arg]
    app_with_db.dependency_overrides[get_settings] = lambda: unset
    api = TestClient(app_with_db)
    _add_vehicle(db)
    response = _post_fixture(api, "webhook_vehicle_state.json")
    assert response.status_code == 401
    assert _count(db) == 0


# ---------------------------------------------------------------------------
# VERIFY (step 2)
# ---------------------------------------------------------------------------


def test_signed_verify_answers_challenge(api: TestClient) -> None:
    response = _post_fixture(api, "webhook_verify_challenge.json")
    assert response.status_code == 200
    challenge = fixture_json("webhook_verify_challenge.json")["data"]["challenge"]
    expected = compute_signature(challenge.encode(), TEST_MANAGEMENT_TOKEN)
    assert response.json() == {"challenge": expected}
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("signature", [None, "0" * 64, "not-hex"])
def test_unsigned_or_badly_signed_verify_gets_no_answer(
    api: TestClient, signature: str | None
) -> None:
    response = _post(api, fixture_bytes("webhook_verify_challenge.json"), signature=signature)
    assert response.status_code == 401
    assert "challenge" not in response.json()


def test_verify_without_challenge_is_400(api: TestClient) -> None:
    payload = fixture_json("webhook_verify_challenge.json")
    payload["data"] = {}
    response = _post(api, _encode(payload))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WEBHOOK_PAYLOAD_INVALID"


# ---------------------------------------------------------------------------
# Parsing (step 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[1, 2]", b'{"data": {}}', b"\xff\xfe"],
    ids=["not-json", "array", "no-event-type", "not-utf8"],
)
def test_signed_malformed_body_is_400(api: TestClient, db: Session, body: bytes) -> None:
    response = _post(api, body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WEBHOOK_PAYLOAD_INVALID"
    assert _count(db) == 0


def test_vehicle_state_without_vehicle_is_400(api: TestClient, db: Session) -> None:
    payload = fixture_json("webhook_vehicle_state.json")
    del payload["data"]["vehicle"]
    response = _post(api, _encode(payload))
    assert response.status_code == 400
    assert _count(db) == 0


def test_vehicle_state_without_event_id_is_400(api: TestClient, db: Session) -> None:
    _add_vehicle(db)
    payload = fixture_json("webhook_vehicle_state.json")
    del payload["eventId"]
    response = _post(api, _encode(payload))
    assert response.status_code == 400
    assert _count(db) == 0


def test_unknown_event_type_acknowledged_without_writes(api: TestClient, db: Session) -> None:
    _add_vehicle(db)
    payload = fixture_json("webhook_vehicle_state.json")
    payload["eventType"] = "SOMETHING_NEW"
    response = _post(api, _encode(payload))
    assert response.status_code == 200
    assert response.json() == {"accepted": False, "event_id": "evt-0002"}
    assert _count(db) == 0


# ---------------------------------------------------------------------------
# Ingest (steps 4-6)
# ---------------------------------------------------------------------------


def test_valid_event_writes_telemetry_and_latest_state(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    response = _post_fixture(api, "webhook_vehicle_state.json")
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_id": "evt-0002"}

    rows = {row.signal_code: row for row in _telemetry(db)}
    assert set(rows) == {"soc", "range", "charging_status"}
    soc = rows["soc"]
    assert soc.vehicle_id == vehicle.id
    assert soc.source == "smartcar"
    assert soc.source_event_id == "evt-0002"
    assert soc.value == Decimal("64")
    assert soc.unit == "percent"
    assert soc.recorded_at == T_EVT2
    assert soc.received_at is not None
    assert soc.is_simulated is False
    assert soc.raw_payload["signal"]["code"] == "tractionbattery-stateofcharge"
    assert rows["range"].value == Decimal("241.5")
    assert rows["range"].unit == "km"
    assert rows["charging_status"].value == Decimal("1")

    state = _reload(db, vehicle).latest_state
    assert state is not None
    assert state["recorded_at"] == _iso(T_EVT2)
    assert "received_at" in state
    assert state["signals"] == {
        "soc": {"value": 64, "unit": "percent", "recorded_at": _iso(T_EVT2)},
        "range": {"value": 241.5, "unit": "km", "recorded_at": _iso(T_EVT2)},
        "charging_status": {"value": True, "unit": "bool", "recorded_at": _iso(T_EVT2)},
    }


def test_supported_signals_grow_from_first_data(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db, supported_signals=["odometer"])
    _post_fixture(api, "webhook_vehicle_state.json")
    assert _reload(db, vehicle).supported_signals == [
        "charging_status",
        "odometer",
        "range",
        "soc",
    ]


def test_duplicate_event_is_idempotent(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    first = _post_fixture(api, "webhook_vehicle_state.json")
    count = _count(db)
    state = _reload(db, vehicle).latest_state

    second = _post_fixture(api, "webhook_vehicle_state.json")
    assert first.status_code == second.status_code == 200
    assert second.json() == {"accepted": True, "event_id": "evt-0002"}
    assert _count(db) == count == 3
    assert _reload(db, vehicle).latest_state == state


def test_out_of_order_keeps_newer_values(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    assert _post_fixture(api, "webhook_vehicle_state.json").status_code == 200  # SoC 64 @ T2
    assert _post_fixture(api, "webhook_out_of_order_older.json").status_code == 200  # SoC 61 @ T1

    soc_rows = [row for row in _telemetry(db) if row.signal_code == "soc"]
    assert [(row.value, row.recorded_at) for row in soc_rows] == [
        (Decimal("61"), T_EVT1),
        (Decimal("64"), T_EVT2),
    ]
    state = _reload(db, vehicle).latest_state
    assert state is not None
    assert state["signals"]["soc"] == {"value": 64, "unit": "percent", "recorded_at": _iso(T_EVT2)}
    assert state["recorded_at"] == _iso(T_EVT2)


def test_partial_event_only_updates_its_signals(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    _post_fixture(api, "webhook_vehicle_state.json")  # soc, range, charging @ T2
    _post_fixture(api, "webhook_unknown_signal.json")  # soc 65 + unknown @ T3

    state = _reload(db, vehicle).latest_state
    assert state is not None
    assert state["signals"]["soc"] == {"value": 65, "unit": "percent", "recorded_at": _iso(T_EVT3)}
    assert state["signals"]["range"]["recorded_at"] == _iso(T_EVT2)
    assert state["signals"]["range"]["value"] == 241.5
    assert state["signals"]["charging_status"]["value"] is True
    assert state["recorded_at"] == _iso(T_EVT3)


def test_unknown_signal_stored_raw_but_not_in_latest_state(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    _post_fixture(api, "webhook_unknown_signal.json")

    rows = {row.signal_code: row for row in _telemetry(db)}
    assert set(rows) == {"soc", "future-newsignal"}
    unknown = rows["future-newsignal"]
    assert unknown.value is None
    assert unknown.raw_payload["signal"]["body"] == {"value": "SOMETHING"}
    state = _reload(db, vehicle).latest_state
    assert state is not None
    assert set(state["signals"]) == {"soc"}
    assert "future-newsignal" not in _reload(db, vehicle).supported_signals


def test_errored_signal_stored_but_latest_state_kept(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    _post_fixture(api, "webhook_vehicle_state.json")

    payload = fixture_json("webhook_unknown_signal.json")
    payload["eventType"] = "VEHICLE_ERROR"
    payload["eventId"] = "evt-err-1"
    payload["data"]["signals"] = [
        {
            "code": "tractionbattery-range",
            "status": {"value": "ERROR", "error": {"code": "VEHICLE_NOT_CAPABLE"}},
            "meta": {"oemUpdatedAt": 1794261780000},
        }
    ]
    response = _post(api, _encode(payload))
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_id": "evt-err-1"}

    error_rows = [row for row in _telemetry(db) if row.source_event_id == "evt-err-1"]
    assert len(error_rows) == 1
    assert error_rows[0].signal_code == "range"
    assert error_rows[0].value is None
    assert error_rows[0].raw_payload["outcome"] == "error"
    assert error_rows[0].raw_payload["error_code"] == "VEHICLE_NOT_CAPABLE"
    state = _reload(db, vehicle).latest_state
    assert state is not None
    assert state["signals"]["range"] == {"value": 241.5, "unit": "km", "recorded_at": _iso(T_EVT2)}


def test_unparseable_vehicle_error_is_acknowledged(api: TestClient, db: Session) -> None:
    _add_vehicle(db)
    payload = {"eventId": "evt-err-2", "eventType": "VEHICLE_ERROR", "data": {"other": 1}}
    response = _post(api, _encode(payload))
    assert response.status_code == 200
    assert response.json() == {"accepted": False, "event_id": "evt-err-2"}
    assert _count(db) == 0


def test_signal_without_timestamp_falls_back_to_delivered_at(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    payload = fixture_json("webhook_unknown_signal.json")
    payload["data"]["signals"] = [copy.deepcopy(payload["data"]["signals"][0])]
    payload["data"]["signals"][0]["meta"] = {}
    response = _post(api, _encode(payload))
    assert response.status_code == 200
    delivered = datetime.fromtimestamp(payload["meta"]["deliveredAt"] / 1000, tz=UTC)
    (row,) = _telemetry(db, vehicle)
    assert row.recorded_at == delivered


def test_signal_without_any_timestamp_is_skipped(api: TestClient, db: Session) -> None:
    _add_vehicle(db)
    payload = fixture_json("webhook_unknown_signal.json")
    payload["data"]["signals"][0]["meta"] = {}
    payload["data"]["signals"][1]["meta"] = {}
    del payload["meta"]["deliveredAt"]
    response = _post(api, _encode(payload))
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert _count(db) == 0


def test_disconnected_vehicle_not_accepted(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db, status="disconnected")
    response = _post_fixture(api, "webhook_vehicle_state.json")
    assert response.status_code == 200
    assert response.json() == {"accepted": False, "event_id": "evt-0002"}
    assert _count(db) == 0
    assert _reload(db, vehicle).latest_state is None


def test_pending_vehicle_not_accepted(api: TestClient, db: Session) -> None:
    _add_vehicle(db, status="pending")
    response = _post_fixture(api, "webhook_vehicle_state.json")
    assert response.json()["accepted"] is False
    assert _count(db) == 0


def test_unknown_vehicle_not_accepted(api: TestClient, db: Session) -> None:
    _add_vehicle(db, smartcar_vehicle_id="some-other-vehicle")
    response = _post_fixture(api, "webhook_vehicle_state.json")
    assert response.status_code == 200
    assert response.json() == {"accepted": False, "event_id": "evt-0002"}
    assert _count(db) == 0


def test_vehicle_matched_on_user_too(api: TestClient, db: Session) -> None:
    # Same Smartcar vehicle ID under a different Smartcar user must not match.
    _add_vehicle(db, smartcar_user_id="another-smartcar-user")
    response = _post_fixture(api, "webhook_vehicle_state.json")
    assert response.json()["accepted"] is False
    assert _count(db) == 0


def test_simulated_vehicle_marks_rows_simulated(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    vehicle.is_simulated = True
    db.commit()
    _post_fixture(api, "webhook_vehicle_state.json")
    assert {row.is_simulated for row in _telemetry(db)} == {True}


# ---------------------------------------------------------------------------
# Failure (step 7) and logging
# ---------------------------------------------------------------------------


def test_db_failure_returns_5xx(
    api: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_vehicle(db)

    def _boom(*_: Any, **__: Any) -> Any:
        raise OperationalError("INSERT", {}, Exception("connection lost"))

    monkeypatch.setattr(webhook_router, "ingest_vehicle_event", _boom)
    response = _post_fixture(api, "webhook_vehicle_state.json")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert _count(db) == 0


def test_db_failure_log_carries_no_sql_parameters_or_driver_detail(
    api: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _add_vehicle(db)
    secret_location = "40.712776,-74.005974"

    class _DriverError(Exception):
        sqlstate = "23505"

    def _boom(*_: Any, **__: Any) -> Any:
        raise IntegrityError(
            "INSERT INTO vehicle_telemetry ... VALUES (%(raw_payload)s)",
            {"raw_payload": {"vehicle": SC_VEHICLE, "user": SC_USER, "loc": secret_location}},
            _DriverError(f"DETAIL: Key (source_event_id)=({SC_VEHICLE}) already exists"),
        )

    monkeypatch.setattr(webhook_router, "ingest_vehicle_event", _boom)
    caplog.set_level(logging.DEBUG)
    response = _post_fixture(api, "webhook_vehicle_state.json")
    assert response.status_code == 500

    (record,) = [r for r in caplog.records if r.getMessage() == "smartcar_webhook_ingest_failed"]
    assert record.exc_info is None
    assert record.__dict__["error_type"] == "IntegrityError"
    assert record.__dict__["sqlstate"] == "23505"
    for r in caplog.records:
        rendered = f"{r.getMessage()} {r.__dict__}"
        for leaked in (SC_USER, SC_VEHICLE, secret_location, "INSERT INTO"):
            assert leaked not in rendered


def test_engine_hides_sql_parameters() -> None:
    get_engine.cache_clear()
    try:
        assert get_engine().hide_parameters is True
    finally:
        get_engine().dispose()
        get_engine.cache_clear()


def test_logs_never_carry_smartcar_ids_or_values(
    api: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _add_vehicle(db)
    caplog.set_level(logging.DEBUG)
    _post_fixture(api, "webhook_vehicle_state.json")
    _post(api, fixture_bytes("webhook_vehicle_state.json"), signature="0" * 64)
    for record in caplog.records:
        rendered = f"{record.getMessage()} {record.__dict__}"
        assert SC_USER not in rendered
        assert SC_VEHICLE not in rendered
        assert TEST_MANAGEMENT_TOKEN not in rendered


# ---------------------------------------------------------------------------
# apply_to_latest_state (pure)
# ---------------------------------------------------------------------------


def _signals(name: str) -> list[Any]:
    return normalize_signals(VehicleStateData.model_validate(fixture_json(name)["data"]))


def test_apply_equal_timestamp_takes_newer_delivery() -> None:
    received = datetime(2026, 11, 10, tzinfo=UTC)
    current = {
        "recorded_at": _iso(T_EVT2),
        "signals": {"soc": {"value": 50, "unit": "percent", "recorded_at": _iso(T_EVT2)}},
    }
    state = apply_to_latest_state(
        current, _signals("webhook_vehicle_state.json"), received_at=received
    )
    assert state is not None
    assert state["signals"]["soc"]["value"] == 64


def test_apply_nothing_new_returns_current_unchanged() -> None:
    current = {
        "recorded_at": _iso(T_EVT3),
        "received_at": "2026-11-10T00:00:00Z",
        "signals": {"soc": {"value": 70, "unit": "percent", "recorded_at": _iso(T_EVT3)}},
    }
    state = apply_to_latest_state(
        current, _signals("webhook_out_of_order_older.json"), received_at=datetime.now(UTC)
    )
    assert state == current


def test_apply_tolerates_malformed_current_state() -> None:
    state = apply_to_latest_state(
        {"signals": "garbage"},
        _signals("webhook_out_of_order_older.json"),
        received_at=datetime.now(UTC),
    )
    assert state is not None
    assert state["signals"]["soc"]["value"] == 61
