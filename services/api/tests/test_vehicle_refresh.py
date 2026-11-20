"""Tests for ``POST /api/v1/vehicles/{id}/refresh`` (api §3.5a, ADR 0010).

Smartcar is a real ``SmartcarClient`` over ``httpx.MockTransport``; the
database is the migrated Postgres test database. Covers ownership, the
per-vehicle rate limit, upstream failures, ingest/dedup against webhook rows
in both directions, the post-ingest hooks, and that ``GET .../state`` still
never calls Smartcar.
"""

from __future__ import annotations

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
from sqlalchemy import Connection, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.integrations.smartcar.client import SmartcarClient, get_smartcar_client
from app.integrations.smartcar.schemas import SignalsResponse, WebhookEvent
from app.modules.users.models import Profile
from app.modules.vehicles.ingest_hooks import (
    TelemetryIngested,
    register_ingest_hook,
    unregister_ingest_hook,
)
from app.modules.vehicles.models import Vehicle, VehicleRefresh, VehicleTelemetry
from app.modules.vehicles.refresh import RefreshLimits, next_allowed_at, refresh_vehicle
from app.modules.vehicles.telemetry_service import (
    ingest_polled_signals,
    ingest_vehicle_event,
    poll_event_id,
)
from tests.conftest import TEST_USER_ID, make_token
from tests.integrations.conftest import fixture_json

SC_USER_ID = "sc-user-refresh"
SC_VEHICLE_ID = "sc-vehicle-refresh"
OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _url(vehicle_id: uuid.UUID) -> str:
    return f"/api/v1/vehicles/{vehicle_id}/refresh"


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def rest_signal(code: str, body: dict[str, Any] | None, at: datetime | None) -> dict[str, Any]:
    meta: dict[str, Any] = {"ingestedAt": _iso(datetime.now(UTC))}
    if at is not None:
        meta |= {"oemUpdatedAt": _iso(at), "retrievedAt": _iso(at + timedelta(seconds=40))}
    status: dict[str, Any] = {"value": "SUCCESS"}
    if body is None:
        status = {"value": "ERROR", "error": {"code": "NOT_CHARGING", "type": "VEHICLE_STATE"}}
    return {
        "id": f"sig-{code}",
        "type": "signal",
        "attributes": {"code": code, "status": status, "body": body},
        "meta": meta,
    }


def signals_payload(
    at: datetime, *, soc: float = 64, odometer: float = 24700.5, charging: bool = False
) -> dict[str, Any]:
    data = [
        rest_signal("tractionbattery-stateofcharge", {"unit": "percent", "value": soc}, at),
        rest_signal("odometer-traveleddistance", {"unit": "km", "value": odometer}, at),
        rest_signal("tractionbattery-range", {"unit": "km", "value": 240.0}, at),
        rest_signal("charge-ischarging", {"value": charging}, at),
        # Errored, no timestamps: skipped on a refresh (no delivery time to fall back on).
        rest_signal("charge-timetocomplete", None, None),
    ]
    return {"data": data, "meta": {"pageNumber": 1, "pageSize": 5, "totalCount": 5}}


@dataclass
class FakeSmartcar:
    signals: dict[str, Any] = field(default_factory=lambda: fixture_json("signals_bz.json"))
    signals_status: int = 200
    signals_body: dict[str, Any] | None = None
    signals_headers: dict[str, str] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json=fixture_json("token_response.json"))
        if request.url.path.endswith("/signals"):
            if self.signals_status != 200:
                return httpx.Response(
                    self.signals_status,
                    json=self.signals_body or {"errors": []},
                    headers=self.signals_headers,
                )
            return httpx.Response(200, json=self.signals)
        return httpx.Response(404, json={"errors": []})

    @property
    def signal_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/signals")]


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
        smartcar_application_id="test-application-id",
        smartcar_api_base_url="https://vehicle.test/v3",
        smartcar_auth_url="https://iam.test/oauth2/token",
        smartcar_connect_url="https://connect.test/oauth/authorize",
    )


@pytest.fixture()
def api(app_with_db: FastAPI, settings: Settings, smartcar: FakeSmartcar) -> TestClient:
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    client = SmartcarClient.from_settings(settings, transport=httpx.MockTransport(smartcar.handler))
    app_with_db.dependency_overrides[get_smartcar_client] = lambda: client
    return TestClient(app_with_db)


@pytest.fixture()
def db(db_connection: Connection) -> Iterator[Session]:
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def auth(key_material: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(key_material)}"}


def _profile(db: Session, user_id: uuid.UUID = TEST_USER_ID) -> None:
    if db.get(Profile, user_id) is None:
        db.add(Profile(id=user_id))
        db.flush()


def add_vehicle(
    db: Session,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    status: str = "connected",
    simulated: bool = False,
) -> Vehicle:
    _profile(db, user_id)
    vehicle = Vehicle(
        user_id=user_id,
        smartcar_vehicle_id=None if simulated else SC_VEHICLE_ID,
        smartcar_user_id=None if simulated else SC_USER_ID,
        make="Toyota",
        model="bZ XLE",
        year=2026,
        connection_status=status,
        connected_at=datetime.now(UTC),
        supported_signals=[],
        is_simulated=simulated,
    )
    db.add(vehicle)
    db.commit()
    return vehicle


@pytest.fixture()
def vehicle(db: Session) -> Vehicle:
    return add_vehicle(db)


def telemetry(db: Session, vehicle: Vehicle) -> list[VehicleTelemetry]:
    db.expire_all()
    return list(
        db.scalars(
            select(VehicleTelemetry)
            .where(VehicleTelemetry.vehicle_id == vehicle.id)
            .order_by(VehicleTelemetry.signal_code, VehicleTelemetry.received_at)
        )
    )


def attempts(db: Session, vehicle: Vehicle) -> list[VehicleRefresh]:
    db.expire_all()
    return list(
        db.scalars(
            select(VehicleRefresh)
            .where(VehicleRefresh.vehicle_id == vehicle.id)
            .order_by(VehicleRefresh.requested_at)
        )
    )


def add_attempt(db: Session, vehicle: Vehicle, ago: timedelta, outcome: str = "ok") -> None:
    db.add(
        VehicleRefresh(vehicle_id=vehicle.id, requested_at=datetime.now(UTC) - ago, outcome=outcome)
    )
    db.commit()


def age_attempts(db: Session, vehicle: Vehicle, by: timedelta) -> None:
    """Move every recorded attempt back in time, so the rate limit lets the next one through."""

    db.execute(
        update(VehicleRefresh)
        .where(VehicleRefresh.vehicle_id == vehicle.id)
        .values(requested_at=VehicleRefresh.requested_at - by)
    )
    db.commit()


def webhook_event(at: datetime, *, soc: float, event_id: str = "evt-1") -> WebhookEvent:
    ms = int(at.timestamp() * 1000)
    return WebhookEvent.model_validate(
        {
            "eventId": event_id,
            "eventType": "VEHICLE_STATE",
            "data": {
                "user": {"id": SC_USER_ID},
                "vehicle": {"id": SC_VEHICLE_ID},
                "signals": [
                    {
                        "code": "tractionbattery-stateofcharge",
                        "body": {"unit": "percent", "value": soc},
                        "meta": {"oemUpdatedAt": ms, "retrievedAt": ms},
                    }
                ],
            },
            "meta": {"deliveredAt": ms + 30_000},
        }
    )


@pytest.fixture()
def spy() -> Iterator[list[TelemetryIngested]]:
    calls: list[TelemetryIngested] = []

    def _spy(event: TelemetryIngested, open_session: Any) -> None:
        calls.append(event)

    register_ingest_hook(_spy)
    try:
        yield calls
    finally:
        unregister_ingest_hook(_spy)


# ---------------------------------------------------------------------------
# Happy path and ingest


def test_refresh_stores_readings_and_returns_fresh_state(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    smartcar.signals = signals_payload(at, soc=64)

    started = datetime.now(UTC)
    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inserted"] == 4
    assert body["state"]["is_stale"] is False
    assert body["state"]["signals"]["soc_percent"] == 64
    assert body["state"]["signals"]["range_km"] == 240.0
    assert body["state"]["recorded_at"] == _iso(at)
    refreshed_at = datetime.fromisoformat(body["refreshed_at"])
    next_at = datetime.fromisoformat(body["next_refresh_at"])
    assert refreshed_at >= started - timedelta(seconds=1)
    assert next_at - refreshed_at == timedelta(minutes=15)

    # One signals call, with the user header.
    assert len(smartcar.signal_calls) == 1
    call = smartcar.signal_calls[0]
    assert call.url.path == f"/v3/vehicles/{SC_VEHICLE_ID}/signals"
    assert call.headers["sc-user-id"] == SC_USER_ID

    rows = telemetry(db, vehicle)
    assert {r.signal_code for r in rows} == {"soc", "odometer", "range", "charging_status"}
    for row in rows:
        assert row.source == "smartcar"
        assert row.recorded_at == at  # the OEM timestamp, not the request time
        assert row.source_event_id == poll_event_id(vehicle.id, at)
        assert row.is_simulated is False
    soc = next(r for r in rows if r.signal_code == "soc")
    assert soc.value == Decimal("64")
    assert soc.unit == "percent"
    assert soc.raw_payload["smartcar_code"] == "tractionbattery-stateofcharge"

    db.expire_all()
    fresh = db.get(Vehicle, vehicle.id)
    assert fresh is not None
    assert fresh.latest_state is not None
    assert fresh.latest_state["signals"]["soc"]["value"] == 64
    assert set(fresh.supported_signals) == {"soc", "odometer", "range", "charging_status"}

    [attempt] = attempts(db, vehicle)
    assert attempt.outcome == "ok"
    assert attempt.inserted == 4
    assert attempt.upstream_status is None


def test_refresh_with_real_bz_fixture_skips_errored_signals(
    api: TestClient, auth: dict[str, str], db: Session, vehicle: Vehicle
) -> None:
    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    # The F0 capture is days old: stored, but the car is still stale.
    assert body["state"]["is_stale"] is True
    assert body["state"]["signals"]["soc_percent"] == 61
    codes = {r.signal_code for r in telemetry(db, vehicle)}
    # Mapped successes plus unmapped successes (kept under Smartcar's code);
    # errored signals carry no timestamp on REST and are skipped.
    assert {"soc", "odometer", "range", "charging_status", "cable_connected"} <= codes
    assert "charge-ischargingportflapopen" in codes
    assert "closure-reartrunk" not in codes
    assert "charge-timetocomplete" not in codes
    assert body["inserted"] == len(codes)


def test_repeat_refresh_of_same_reading_inserts_nothing(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=3)
    smartcar.signals = signals_payload(at)
    assert api.post(_url(vehicle.id), headers=auth).json()["inserted"] == 4
    before = len(telemetry(db, vehicle))

    age_attempts(db, vehicle, timedelta(minutes=16))
    second = api.post(_url(vehicle.id), headers=auth)

    assert second.status_code == 200, second.text
    assert second.json()["inserted"] == 0
    # The car hasn't reported anything newer: still honest about staleness.
    assert second.json()["state"]["is_stale"] is True
    assert len(telemetry(db, vehicle)) == before
    assert [a.inserted for a in attempts(db, vehicle)] == [4, 0]


def test_refresh_skips_reading_a_webhook_already_stored(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    result = ingest_vehicle_event(
        db, webhook_event(at, soc=64), webhook_event(at, soc=64).vehicle_state()
    )
    assert result.inserted == 1
    smartcar.signals = signals_payload(at, soc=64)

    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 200, response.text
    assert response.json()["inserted"] == 3
    soc_rows = [r for r in telemetry(db, vehicle) if r.signal_code == "soc"]
    assert len(soc_rows) == 1
    assert soc_rows[0].source_event_id == "evt-1"


def test_late_webhook_skips_reading_a_refresh_already_stored(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    smartcar.signals = signals_payload(at, soc=64)
    assert api.post(_url(vehicle.id), headers=auth).json()["inserted"] == 4

    # Smartcar retries the delivery that was missed while the API was down.
    event = webhook_event(at, soc=64, event_id="evt-retried")
    result = ingest_vehicle_event(db, event, event.vehicle_state())

    assert result.accepted is True
    assert result.inserted == 0
    soc_rows = [r for r in telemetry(db, vehicle) if r.signal_code == "soc"]
    assert [r.source_event_id for r in soc_rows] == [poll_event_id(vehicle.id, at)]


def test_webhook_duplicates_of_other_webhooks_are_unchanged(db: Session, vehicle: Vehicle) -> None:
    """Webhook-vs-webhook behaviour is unchanged: two events with one OEM reading both store it."""

    at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    for event_id in ("evt-a", "evt-b"):
        event = webhook_event(at, soc=70, event_id=event_id)
        assert ingest_vehicle_event(db, event, event.vehicle_state()).inserted == 1
    assert len([r for r in telemetry(db, vehicle) if r.signal_code == "soc"]) == 2


def test_older_refresh_reading_does_not_move_latest_state_back(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    newer = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=3)
    event = webhook_event(newer, soc=80)
    ingest_vehicle_event(db, event, event.vehicle_state())
    smartcar.signals = signals_payload(newer - timedelta(hours=1), soc=50)

    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 200, response.text
    assert response.json()["state"]["signals"]["soc_percent"] == 80
    assert response.json()["inserted"] == 4  # stored in the history all the same


# ---------------------------------------------------------------------------
# Hooks


def test_refresh_runs_post_ingest_hooks_when_it_stored_rows(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
    spy: list[TelemetryIngested],
) -> None:
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    smartcar.signals = signals_payload(at)

    api.post(_url(vehicle.id), headers=auth)

    assert len(spy) == 1
    assert spy[0].vehicle_id == vehicle.id
    assert spy[0].user_id == TEST_USER_ID
    assert spy[0].signal_codes == frozenset({"soc", "odometer", "range", "charging_status"})
    assert spy[0].earliest_recorded_at == at
    assert spy[0].is_simulated is False

    age_attempts(db, vehicle, timedelta(minutes=16))
    api.post(_url(vehicle.id), headers=auth)
    assert len(spy) == 1  # nothing new, no hooks


# ---------------------------------------------------------------------------
# Who may refresh


def test_refresh_requires_auth(api: TestClient, vehicle: Vehicle, smartcar: FakeSmartcar) -> None:
    response = api.post(_url(vehicle.id))
    assert response.status_code == 401
    assert smartcar.requests == []


def test_refresh_of_someone_elses_vehicle_is_not_found(
    api: TestClient, auth: dict[str, str], db: Session, smartcar: FakeSmartcar
) -> None:
    theirs = add_vehicle(db, user_id=OTHER_USER_ID)

    response = api.post(_url(theirs.id), headers=auth)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert smartcar.requests == []
    assert attempts(db, theirs) == []


@pytest.mark.parametrize(
    ("status", "simulated"), [("disconnected", False), ("pending", False), ("connected", True)]
)
def test_refresh_needs_a_connected_real_vehicle(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    smartcar: FakeSmartcar,
    status: str,
    simulated: bool,
) -> None:
    vehicle = add_vehicle(db, status=status, simulated=simulated)

    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VEHICLE_NOT_CONNECTED"
    assert smartcar.requests == []
    assert attempts(db, vehicle) == []


# ---------------------------------------------------------------------------
# Rate limit


def test_second_refresh_within_interval_is_rate_limited(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    add_attempt(db, vehicle, timedelta(minutes=5))

    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "RATE_LIMITED"
    seconds = error["details"]["retry_after_seconds"]
    assert 590 <= seconds <= 600
    assert response.headers["Retry-After"] == str(seconds)
    assert error["details"]["next_refresh_at"].endswith("Z")
    assert smartcar.requests == []
    assert len(attempts(db, vehicle)) == 1


def test_daily_cap_is_rate_limited_until_oldest_attempt_expires(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    # Eight attempts, the oldest 23 h ago, the newest an hour ago.
    for hours in (23, 20, 17, 14, 11, 8, 5, 1):
        add_attempt(db, vehicle, timedelta(hours=hours))

    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 429
    seconds = response.json()["error"]["details"]["retry_after_seconds"]
    assert 3590 <= seconds <= 3600  # the 23 h-old attempt leaves the window in 1 h
    assert smartcar.requests == []


def test_attempts_older_than_a_day_do_not_count(
    api: TestClient, auth: dict[str, str], db: Session, vehicle: Vehicle
) -> None:
    for hours in range(25, 33):
        add_attempt(db, vehicle, timedelta(hours=hours))

    assert api.post(_url(vehicle.id), headers=auth).status_code == 200


def test_old_attempts_are_pruned_when_a_new_one_is_recorded(
    api: TestClient, auth: dict[str, str], db: Session, vehicle: Vehicle
) -> None:
    add_attempt(db, vehicle, timedelta(days=31))
    add_attempt(db, vehicle, timedelta(days=2))

    assert api.post(_url(vehicle.id), headers=auth).status_code == 200

    ages = [datetime.now(UTC) - a.requested_at for a in attempts(db, vehicle)]
    assert len(ages) == 2
    assert max(ages) < timedelta(days=3)


def test_failed_smartcar_call_counts_against_the_limit(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    smartcar.signals_status = 500

    first = api.post(_url(vehicle.id), headers=auth)
    assert first.status_code == 502
    assert first.json()["error"]["code"] == "SMARTCAR_UNREACHABLE"
    [attempt] = attempts(db, vehicle)
    assert attempt.outcome == "failed"
    assert attempt.upstream_status == 500

    smartcar.signals_status = 200
    second = api.post(_url(vehicle.id), headers=auth)
    assert second.status_code == 429
    assert len(smartcar.signal_calls) == 1


def test_unconfigured_smartcar_does_not_spend_the_allowance(
    app_with_db: FastAPI,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
) -> None:
    settings = Settings(_env_file=None, app_env="test")  # type: ignore[call-arg]
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    app_with_db.dependency_overrides[get_smartcar_client] = lambda: SmartcarClient.from_settings(
        settings, transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )

    response = TestClient(app_with_db).post(_url(vehicle.id), headers=auth)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SMARTCAR_NOT_CONFIGURED"
    assert attempts(db, vehicle) == []


# ---------------------------------------------------------------------------
# Smartcar throttling


def test_smartcar_vehicle_rate_limit_is_passed_on_with_retry_after(
    api: TestClient,
    auth: dict[str, str],
    db: Session,
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    smartcar.signals_status = 429
    smartcar.signals_body = {"type": "RATE_LIMIT", "code": "VEHICLE", "title": "Too Many Requests"}
    smartcar.signals_headers = {"retry-after": "120"}

    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "SMARTCAR_RATE_LIMITED"
    assert error["details"] == {
        "upstream_status": 429,
        "upstream_code": "VEHICLE",
        "retry_after_seconds": 120,
    }
    assert response.headers["Retry-After"] == "120"
    assert attempts(db, vehicle)[0].upstream_status == 429


def test_smartcar_monthly_cap_is_a_bad_gateway(
    api: TestClient, auth: dict[str, str], vehicle: Vehicle, smartcar: FakeSmartcar
) -> None:
    smartcar.signals_status = 429
    smartcar.signals_body = {"errors": [{"type": "BILLING", "code": "VEHICLE_REQUEST_LIMIT"}]}

    response = api.post(_url(vehicle.id), headers=auth)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SMARTCAR_ERROR"
    assert response.json()["error"]["details"]["upstream_code"] == "VEHICLE_REQUEST_LIMIT"


# ---------------------------------------------------------------------------
# The read path stays cache-only (ADR 0002)


def test_get_state_never_calls_smartcar(
    api: TestClient,
    auth: dict[str, str],
    vehicle: Vehicle,
    smartcar: FakeSmartcar,
) -> None:
    for _ in range(3):
        response = api.get(f"/api/v1/vehicles/{vehicle.id}/state", headers=auth)
        assert response.status_code == 200
        assert response.json()["is_stale"] is True
    assert smartcar.requests == []


# ---------------------------------------------------------------------------
# next_allowed_at (pure)

LIMITS = RefreshLimits(min_interval=timedelta(minutes=15), max_per_day=3)
T = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def test_next_allowed_at_without_attempts_is_none() -> None:
    assert next_allowed_at([], limits=LIMITS) is None


def test_next_allowed_at_uses_interval_below_the_cap() -> None:
    attempts_ = [T - timedelta(hours=2), T]
    assert next_allowed_at(attempts_, limits=LIMITS) == T + timedelta(minutes=15)


def test_next_allowed_at_waits_for_oldest_counted_attempt_at_the_cap() -> None:
    attempts_ = [T - timedelta(hours=10), T - timedelta(hours=5), T]
    assert next_allowed_at(attempts_, limits=LIMITS) == T + timedelta(hours=14)


# ---------------------------------------------------------------------------
# Review fixes (PR #66): cross-vehicle poll IDs, stale identity under the lock


def test_two_vehicles_with_the_same_oem_timestamp_both_store(db: Session, vehicle: Vehicle) -> None:
    """The dedup index has no vehicle_id, so the synthetic poll ID must carry it."""

    _profile(db, OTHER_USER_ID)
    other = Vehicle(
        user_id=OTHER_USER_ID,
        smartcar_vehicle_id="sc-vehicle-other",
        smartcar_user_id="sc-user-other",
        make="Toyota",
        model="bZ XLE",
        year=2026,
        connection_status="connected",
        supported_signals=[],
    )
    db.add(other)
    db.commit()
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    signals = SignalsResponse.model_validate(signals_payload(at))

    first = ingest_polled_signals(db, vehicle_id=vehicle.id, signals=signals)
    second = ingest_polled_signals(db, vehicle_id=other.id, signals=signals)

    assert first.inserted == 4
    assert second.inserted == 4
    assert {r.source_event_id for r in telemetry(db, other)} == {poll_event_id(other.id, at)}


def _request_session(conn: Connection) -> Session:
    """A session configured like production ``get_db`` (no expiry on commit)."""

    return Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


def test_refresh_merges_with_state_written_while_smartcar_was_answering(
    db_connection: Connection, vehicle: Vehicle, settings: Settings
) -> None:
    """A webhook landing during the Smartcar call must not be overwritten by stale state."""

    request_db = _request_session(db_connection)
    now = datetime.now(UTC).replace(microsecond=0)
    newer, older = now - timedelta(minutes=1), now - timedelta(hours=1)

    class _Smartcar:
        def get_signals(self, sc_user_id: str, sc_vehicle_id: str) -> SignalsResponse:
            # A webhook request, with its own session, finishes mid-refresh.
            with _request_session(db_connection) as webhook_db:
                event = webhook_event(newer, soc=80, event_id="evt-during-refresh")
                ingest_vehicle_event(webhook_db, event, event.vehicle_state())
            return SignalsResponse.model_validate(signals_payload(older, soc=50))

    try:
        outcome = refresh_vehicle(
            request_db,
            _Smartcar(),  # type: ignore[arg-type]
            user_id=TEST_USER_ID,
            vehicle_id=vehicle.id,
            limits=RefreshLimits(min_interval=timedelta(minutes=15), max_per_day=8),
            stale_after=timedelta(minutes=15),
            now=now,
        )
    finally:
        request_db.close()

    assert outcome.state.signals.soc_percent == 80
    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as check:
        stored = check.get(Vehicle, vehicle.id)
        assert stored is not None and stored.latest_state is not None
        assert stored.latest_state["signals"]["soc"]["value"] == 80
        assert stored.latest_state["signals"]["range"]["value"] == 240


def test_refresh_sees_a_disconnect_made_while_smartcar_was_answering(
    db_connection: Connection, vehicle: Vehicle
) -> None:
    request_db = _request_session(db_connection)
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)

    class _Smartcar:
        def get_signals(self, sc_user_id: str, sc_vehicle_id: str) -> SignalsResponse:
            # The user disconnects in another tab while Smartcar is answering.
            with _request_session(db_connection) as other_db:
                other_db.execute(
                    update(Vehicle)
                    .where(Vehicle.id == vehicle.id)
                    .values(connection_status="disconnected")
                )
                other_db.commit()
            return SignalsResponse.model_validate(signals_payload(at))

    try:
        with pytest.raises(ApiError) as exc:
            refresh_vehicle(
                request_db,
                _Smartcar(),  # type: ignore[arg-type]
                user_id=TEST_USER_ID,
                vehicle_id=vehicle.id,
                limits=RefreshLimits(min_interval=timedelta(minutes=15), max_per_day=8),
                stale_after=timedelta(minutes=15),
            )
    finally:
        request_db.close()

    assert exc.value.code == "VEHICLE_NOT_CONNECTED"
    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as check:
        rows = check.scalars(
            select(VehicleTelemetry).where(VehicleTelemetry.vehicle_id == vehicle.id)
        ).all()
        assert rows == []
