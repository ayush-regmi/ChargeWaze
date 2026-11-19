"""Post-ingest hooks: registry, webhook scheduling, trips end to end (ADR 0009, P5-F3).

Signed webhook deliveries go through the real router. ``TestClient`` runs
background tasks before returning, so the hooks have run (on the same
rolled-back test connection as the request) by the time ``post`` returns.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.integrations.smartcar.signatures import compute_signature
from app.main import create_app
from app.modules.trips import observed_service
from app.modules.trips.observed_models import ObservedTrip
from app.modules.users.models import Profile
from app.modules.vehicles import ingest_hooks
from app.modules.vehicles.ingest_hooks import (
    TelemetryIngested,
    register_ingest_hook,
    registered_ingest_hooks,
    run_ingest_hooks,
    unregister_ingest_hook,
)
from app.modules.vehicles.models import Vehicle
from tests.conftest import TEST_USER_ID
from tests.integrations.conftest import TEST_MANAGEMENT_TOKEN

WEBHOOK = "/api/v1/webhooks/smartcar"
SC_USER = "sc-user-hooks"
SC_VEHICLE = "sc-vehicle-hooks"
T0 = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)

Calls = list[TelemetryIngested]


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        smartcar_management_token=TEST_MANAGEMENT_TOKEN,
    )


@pytest.fixture()
def api(app_with_db: FastAPI, settings: Settings) -> TestClient:
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app_with_db)


@pytest.fixture()
def db(db_connection: Connection) -> Iterator[Session]:
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def vehicle(db: Session) -> Vehicle:
    db.add(Profile(id=TEST_USER_ID))
    db.flush()
    row = Vehicle(
        user_id=TEST_USER_ID,
        smartcar_vehicle_id=SC_VEHICLE,
        smartcar_user_id=SC_USER,
        make="Toyota",
        model="bZ XLE AWD",
        year=2026,
        connection_status="connected",
        connected_at=T0,
        supported_signals=[],
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture()
def spy() -> Iterator[Calls]:
    calls: Calls = []

    def _spy(event: TelemetryIngested, open_session: Any) -> None:
        calls.append(event)

    register_ingest_hook(_spy)
    try:
        yield calls
    finally:
        unregister_ingest_hook(_spy)


def _signal(code: str, body: dict[str, Any], at: datetime) -> dict[str, Any]:
    ms = int(at.timestamp() * 1000)
    return {"code": code, "body": body, "meta": {"oemUpdatedAt": ms, "retrievedAt": ms}}


def delivery(
    minutes: float,
    *,
    odometer: float | None = None,
    soc: float | None = None,
    charging: bool | None = None,
    range_km: float | None = None,
    event_id: str | None = None,
) -> bytes:
    at = T0 + timedelta(minutes=minutes)
    signals = []
    if odometer is not None:
        signals.append(_signal("odometer-traveleddistance", {"unit": "km", "value": odometer}, at))
    if soc is not None:
        signals.append(
            _signal("tractionbattery-stateofcharge", {"unit": "percent", "value": soc}, at)
        )
    if charging is not None:
        signals.append(_signal("charge-ischarging", {"value": charging}, at))
    if range_km is not None:
        signals.append(_signal("tractionbattery-range", {"unit": "km", "value": range_km}, at))
    payload = {
        "eventId": event_id or f"evt-{uuid.uuid4()}",
        "eventType": "VEHICLE_STATE",
        "data": {
            "user": {"id": SC_USER},
            "vehicle": {"id": SC_VEHICLE, "make": "TOYOTA", "model": "bZ XLE AWD", "year": 2026},
            "signals": signals,
            "triggers": [],
        },
        "meta": {
            "deliveryId": f"delivery-{uuid.uuid4()}",
            "deliveredAt": int((at + timedelta(seconds=30)).timestamp() * 1000),
            "webhookId": "test-webhook-id",
            "version": "4.0",
            "mode": "LIVE",
        },
    }
    return json.dumps(payload).encode()


def post(api: TestClient, body: bytes) -> Any:
    headers = {
        "Content-Type": "application/json",
        "SC-Signature": compute_signature(body, TEST_MANAGEMENT_TOKEN),
    }
    return api.post(WEBHOOK, content=body, headers=headers)


def legs(db: Session, vehicle: Vehicle) -> list[ObservedTrip]:
    db.expire_all()
    return list(
        db.scalars(
            select(ObservedTrip)
            .where(ObservedTrip.vehicle_id == vehicle.id)
            .order_by(ObservedTrip.start_time)
        )
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_trips_hook_is_registered_exactly_once() -> None:
    create_app()
    create_app()
    hooks = registered_ingest_hooks()
    assert hooks.count(observed_service.on_telemetry_ingested) == 1


def test_register_is_idempotent_and_unregister_removes() -> None:
    def hook(event: TelemetryIngested, open_session: Any) -> None:
        return None

    register_ingest_hook(hook)
    register_ingest_hook(hook)
    try:
        assert registered_ingest_hooks().count(hook) == 1
    finally:
        unregister_ingest_hook(hook)
    assert hook not in registered_ingest_hooks()


def test_one_failing_hook_doesnt_stop_the_next(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    def boom(event: TelemetryIngested, open_session: Any) -> None:
        raise RuntimeError("secret value 24650.3")

    def ok(event: TelemetryIngested, open_session: Any) -> None:
        seen.append("ok")

    event = TelemetryIngested(
        vehicle_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        signal_codes=frozenset({"odometer"}),
        earliest_recorded_at=T0,
        is_simulated=False,
    )
    opener: Callable[[], AbstractContextManager[Session]] = lambda: None  # type: ignore[assignment,return-value]  # noqa: E731
    with caplog.at_level(logging.ERROR, logger=ingest_hooks.__name__):
        run_ingest_hooks(event, opener, (boom, ok))

    assert seen == ["ok"]
    (record,) = [r for r in caplog.records if r.getMessage() == "ingest_hook_failed"]
    assert record.error_type == "RuntimeError"  # type: ignore[attr-defined]
    assert "24650" not in caplog.text  # never the exception message


# ---------------------------------------------------------------------------
# Webhook → hooks
# ---------------------------------------------------------------------------


def test_webhook_creates_legs_with_no_other_call(
    api: TestClient, db: Session, vehicle: Vehicle
) -> None:
    assert post(api, delivery(0, odometer=1000.0, soc=80, charging=False)).json()["accepted"]
    assert legs(db, vehicle) == []

    post(api, delivery(5, odometer=1010.0, soc=78))
    (leg,) = legs(db, vehicle)
    assert leg.closed is False
    assert leg.odometer_end_km == Decimal("1010.00")

    post(api, delivery(10, odometer=1020.0, soc=75))
    post(api, delivery(40, odometer=1020.0, soc=75))  # 30 min at rest closes it

    (leg,) = legs(db, vehicle)
    assert leg.closed is True
    assert (leg.odometer_start_km, leg.odometer_end_km) == (
        Decimal("1000.00"),
        Decimal("1020.00"),
    )
    assert leg.energy_kwh == Decimal("3.550")  # 5 points x 71.0 kWh
    assert leg.review_status == "auto"


def test_webhook_event_carries_inserted_codes_and_oldest_timestamp(
    api: TestClient, vehicle: Vehicle, spy: Calls
) -> None:
    post(api, delivery(7, odometer=1000.0, soc=80, range_km=300))

    (event,) = spy
    assert event.vehicle_id == vehicle.id
    assert event.user_id == vehicle.user_id
    assert event.signal_codes == frozenset({"odometer", "soc", "range"})
    assert event.earliest_recorded_at == T0 + timedelta(minutes=7)
    assert event.is_simulated is False


def test_range_only_delivery_doesnt_run_inference(
    api: TestClient,
    db: Session,
    vehicle: Vehicle,
    spy: Calls,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs: list[Any] = []
    monkeypatch.setattr(observed_service, "run_for_vehicle", lambda *a, **k: runs.append(k))

    post(api, delivery(0, range_km=300))

    assert [e.signal_codes for e in spy] == [frozenset({"range"})]
    assert runs == []


def test_duplicate_delivery_doesnt_rerun(api: TestClient, vehicle: Vehicle, spy: Calls) -> None:
    body = delivery(0, odometer=1000.0, soc=80, event_id="evt-dup")
    assert post(api, body).status_code == 200
    assert post(api, body).status_code == 200
    assert len(spy) == 1


def test_unknown_vehicle_schedules_nothing(api: TestClient, spy: Calls) -> None:
    res = post(api, delivery(0, odometer=1000.0))
    assert res.json()["accepted"] is False
    assert spy == []


def test_hook_failure_still_answers_200(
    api: TestClient,
    db: Session,
    vehicle: Vehicle,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("inference blew up")

    monkeypatch.setattr(observed_service, "run_for_vehicle", broken)

    with caplog.at_level(logging.ERROR):
        res = post(api, delivery(0, odometer=1000.0, soc=80))

    assert res.status_code == 200
    assert res.json()["accepted"] is True
    assert any(r.getMessage() == "ingest_hook_failed" for r in caplog.records)
    assert legs(db, vehicle) == []


def test_late_charging_delivery_nulls_a_closed_legs_energy(
    api: TestClient, db: Session, vehicle: Vehicle
) -> None:
    post(api, delivery(0, odometer=1000.0, soc=80, charging=False))
    post(api, delivery(10, odometer=1010.0, soc=75))
    post(api, delivery(50, odometer=1010.0, soc=75))
    post(api, delivery(60, odometer=1020.0, soc=70))
    post(api, delivery(100, odometer=1020.0, soc=70))
    a, b = legs(db, vehicle)
    assert a.closed and a.energy_kwh == Decimal("3.550")

    # Charging events from inside leg A, delivered late.
    post(api, delivery(4, charging=True))
    post(api, delivery(6, charging=False))

    a, b = legs(db, vehicle)
    assert a.energy_kwh is None
    assert "soc_increased" in a.quality_reasons
    assert b.energy_kwh == Decimal("3.550")


def test_late_drive_before_every_leg_is_created_by_the_hook(
    api: TestClient, db: Session, vehicle: Vehicle
) -> None:
    post(api, delivery(0, odometer=1000.0, soc=80, charging=False))
    post(api, delivery(10, odometer=1010.0, soc=75))
    post(api, delivery(50, odometer=1010.0, soc=75))
    (later,) = legs(db, vehicle)
    assert later.closed
    db.expunge(later)

    # A delayed 07:00 → 07:10 drive arrives after the 08:00 leg is stored.
    post(api, delivery(-60, odometer=990.0, soc=88))
    post(api, delivery(-50, odometer=1000.0, soc=84))

    earlier, later_again = legs(db, vehicle)
    assert earlier.start_time == T0 - timedelta(minutes=60)
    assert (earlier.odometer_start_km, earlier.odometer_end_km) == (
        Decimal("990.00"),
        Decimal("1000.00"),
    )
    # The first late reading alone (07:00, 990 km) briefly joins both drives
    # into one bounded leg; the 07:10 reading splits them again, so the 08:00
    # leg is back on its own odometer key (not necessarily its old row).
    assert (later_again.odometer_start_km, later_again.odometer_end_km) == (
        Decimal("1000.00"),
        Decimal("1010.00"),
    )
    assert later_again.start_time == T0
