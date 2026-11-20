"""``DELETE /api/v1/me`` (P6-F5, api §3.1b).

Covers the cascade across every user-owned table, the Smartcar and Supabase
failure policy (deletion proceeds; owed work goes on the tombstone), the
tombstone that stops a still-valid token from recreating the profile, and the
paths that could otherwise resurrect telemetry after deletion.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.integrations.smartcar.client import SmartcarClient
from app.integrations.smartcar.schemas import SignalsResponse
from app.integrations.smartcar.signatures import compute_signature
from app.integrations.supabase.admin import SupabaseAdminClient
from app.modules.trips.observed_models import ObservedTrip
from app.modules.trips.observed_service import on_telemetry_ingested
from app.modules.users.models import Profile
from app.modules.users.privacy import delete_account
from app.modules.users.privacy_models import AccountDeletion
from app.modules.vehicles.ingest_hooks import TelemetryIngested, run_ingest_hooks
from app.modules.vehicles.models import Vehicle, VehicleTelemetry
from app.modules.vehicles.telemetry_service import ingest_polled_signals
from tests.conftest import TEST_USER_ID
from tests.integrations.conftest import TEST_MANAGEMENT_TOKEN, fixture_json
from tests.privacy.conftest import (
    BASE_TIME,
    OTHER_USER_ID,
    FakeSmartcar,
    FakeSupabase,
    SeededAccount,
)

ME = "/api/v1/me"

# Every table a user's data lives in. A new table that cascades from
# ``profiles`` must be added here (and to the row-count checks below).
USER_OWNED_TABLES = {
    "profiles",
    "user_preferences",
    "consent_records",
    "favorite_stations",
    "vehicles",
    "vehicle_specs",
    "vehicle_telemetry",
    "vehicle_refreshes",
    "planned_trips",
    "planned_stops",
    "observed_trips",
    "prediction_results",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _counts(conn: Connection | Session, seeded: SeededAccount) -> dict[str, int]:
    """Rows still present for ``seeded``, per user-owned table."""

    vehicles = [seeded.vehicle_id, seeded.simulated_vehicle_id]
    params: dict[str, Any] = {"u": seeded.user_id, "v": vehicles, "t": seeded.trip_id}
    queries = {
        "profiles": "SELECT count(*) FROM profiles WHERE id = :u",
        "user_preferences": "SELECT count(*) FROM user_preferences WHERE user_id = :u",
        "consent_records": "SELECT count(*) FROM consent_records WHERE user_id = :u",
        "favorite_stations": "SELECT count(*) FROM favorite_stations WHERE user_id = :u",
        "vehicles": "SELECT count(*) FROM vehicles WHERE user_id = :u OR id = ANY(:v)",
        "vehicle_specs": "SELECT count(*) FROM vehicle_specs WHERE vehicle_id = ANY(:v)",
        "vehicle_telemetry": "SELECT count(*) FROM vehicle_telemetry WHERE vehicle_id = ANY(:v)",
        "vehicle_refreshes": "SELECT count(*) FROM vehicle_refreshes WHERE vehicle_id = ANY(:v)",
        "planned_trips": "SELECT count(*) FROM planned_trips WHERE user_id = :u OR id = :t",
        "planned_stops": "SELECT count(*) FROM planned_stops WHERE trip_id = :t",
        "observed_trips": (
            "SELECT count(*) FROM observed_trips WHERE user_id = :u OR vehicle_id = ANY(:v)"
        ),
        "prediction_results": (
            "SELECT count(*) FROM prediction_results WHERE user_id = :u OR vehicle_id = ANY(:v)"
        ),
    }
    assert set(queries) == USER_OWNED_TABLES
    return {
        table: int(conn.execute(text(sql), params).scalar_one()) for table, sql in queries.items()
    }


def _tombstone(db: Session, user_id: uuid.UUID = TEST_USER_ID) -> AccountDeletion | None:
    db.expire_all()
    return db.get(AccountDeletion, user_id)


def _webhook_for(seeded: SeededAccount, event_id: str) -> bytes:
    payload = copy.deepcopy(fixture_json("webhook_vehicle_state.json"))
    payload["eventId"] = event_id
    payload["data"]["user"]["id"] = seeded.smartcar_user_id
    payload["data"]["vehicle"]["id"] = seeded.smartcar_vehicle_id
    return json.dumps(payload).encode()


def _post_webhook(api: TestClient, body: bytes) -> Any:
    return api.post(
        "/api/v1/webhooks/smartcar",
        content=body,
        headers={
            "Content-Type": "application/json",
            "SC-Signature": compute_signature(body, TEST_MANAGEMENT_TOKEN),
        },
    )


# ---------------------------------------------------------------------------
# Schema: every user-owned table cascades from profiles
# ---------------------------------------------------------------------------


def _foreign_keys(conn: Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        text(
            """
            SELECT con.conrelid::regclass::text, con.confrelid::regclass::text, con.confdeltype
            FROM pg_constraint con
            WHERE con.contype = 'f' AND con.connamespace = 'public'::regnamespace
            """
        )
    ).all()
    return [(child, parent, action) for child, parent, action in rows]


def test_every_user_owned_table_cascades_from_profiles(db_connection: Connection) -> None:
    fks = _foreign_keys(db_connection)
    reachable = {"profiles"}
    changed = True
    while changed:
        changed = False
        for child, parent, action in fks:
            if action == "c" and parent in reachable and child not in reachable:
                reachable.add(child)
                changed = True
    # A new user-data table must be added to USER_OWNED_TABLES on purpose.
    assert reachable == USER_OWNED_TABLES

    # Nothing may block the cascade (NO ACTION / RESTRICT into a deleted table).
    blocking = [
        (child, parent) for child, parent, action in fks if parent in reachable and action in "ar"
    ]
    assert blocking == []

    # Any public table with a user_id column is user data and must cascade,
    # except the deletion tombstone, which outlives the profile on purpose.
    with_user_id = set(
        db_connection.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'user_id'"
            )
        ).scalars()
    )
    assert with_user_id - reachable == {"account_deletions"}


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


def test_delete_me_requires_a_token(api: TestClient) -> None:
    response = api.delete(ME)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_delete_me_removes_every_user_owned_row_and_nothing_else(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
) -> None:
    mine = seed_account(TEST_USER_ID, tag="mine")
    other = seed_account(OTHER_USER_ID, tag="other")
    assert all(count >= 1 for count in _counts(db, mine).values())
    stations_before = db.scalar(text("SELECT count(*) FROM charging_stations"))

    response = api.delete(ME, headers=auth())

    assert response.status_code == 204
    assert response.content == b""
    assert _counts(db, mine) == dict.fromkeys(USER_OWNED_TABLES, 0)
    # The other account and the global station cache are untouched.
    assert all(count >= 1 for count in _counts(db, other).values())
    assert db.scalar(text("SELECT count(*) FROM charging_stations")) == stations_before


def test_delete_me_revokes_every_connection_and_deletes_the_auth_user(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
    smartcar: FakeSmartcar,
    supabase: FakeSupabase,
) -> None:
    seeded = seed_account()
    # A car disconnected while Smartcar was down still owes its revocation.
    db.add(
        Vehicle(
            user_id=TEST_USER_ID,
            smartcar_vehicle_id="sc-vehicle-old",
            smartcar_user_id="sc-user-a",
            smartcar_connection_id="sc-connection-owed",
            make="Toyota",
            model="bZ4X",
            year=2023,
            connection_status="disconnected",
            disconnected_at=BASE_TIME,
            supported_signals=[],
        )
    )
    db.commit()

    assert api.delete(ME, headers=auth()).status_code == 204

    assert sorted(smartcar.deleted_connections) == sorted(
        [seeded.connection_id, "sc-connection-owed"]
    )
    assert supabase.deleted_users == [str(TEST_USER_ID)]
    request = supabase.requests[0]
    assert request.url.path == f"/auth/v1/admin/users/{TEST_USER_ID}"
    assert request.headers["apikey"] == "test-service-role-key"
    assert request.headers["authorization"] == "Bearer test-service-role-key"

    tombstone = _tombstone(db)
    assert tombstone is not None
    assert tombstone.auth_user_deleted_at is not None
    assert tombstone.pending_smartcar_connection_ids == []


@pytest.mark.parametrize("failure", ["status_500", "transport"])
def test_smartcar_failure_does_not_block_deletion(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
    smartcar: FakeSmartcar,
    failure: str,
) -> None:
    seeded = seed_account()
    if failure == "transport":
        smartcar.transport_error = True
    else:
        smartcar.delete_status = 500

    assert api.delete(ME, headers=auth()).status_code == 204

    assert _counts(db, seeded) == dict.fromkeys(USER_OWNED_TABLES, 0)
    tombstone = _tombstone(db)
    assert tombstone is not None
    assert tombstone.pending_smartcar_connection_ids == [seeded.connection_id]


def test_smartcar_404_counts_as_already_revoked(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
    smartcar: FakeSmartcar,
) -> None:
    seed_account()
    smartcar.delete_status = 404
    assert api.delete(ME, headers=auth()).status_code == 204
    tombstone = _tombstone(db)
    assert tombstone is not None
    assert tombstone.pending_smartcar_connection_ids == []


@pytest.mark.parametrize("failure", ["status_500", "transport"])
def test_supabase_failure_leaves_the_auth_deletion_owed(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
    supabase: FakeSupabase,
    failure: str,
) -> None:
    seeded = seed_account()
    if failure == "transport":
        supabase.transport_error = True
    else:
        supabase.status = 500

    assert api.delete(ME, headers=auth()).status_code == 204

    assert _counts(db, seeded) == dict.fromkeys(USER_OWNED_TABLES, 0)
    tombstone = _tombstone(db)
    assert tombstone is not None
    assert tombstone.auth_user_deleted_at is None


def test_supabase_404_counts_as_deleted(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    supabase: FakeSupabase,
) -> None:
    supabase.status = 404
    assert api.delete(ME, headers=auth()).status_code == 204
    tombstone = _tombstone(db)
    assert tombstone is not None
    assert tombstone.auth_user_deleted_at is not None


def test_unconfigured_admin_api_is_never_called_and_stays_owed(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
    supabase: FakeSupabase,
    supabase_client: SupabaseAdminClient,
) -> None:
    seed_account()
    supabase_client._key = ""  # as with SUPABASE_SERVICE_ROLE_KEY unset

    assert api.delete(ME, headers=auth()).status_code == 204

    assert supabase.requests == []
    tombstone = _tombstone(db)
    assert tombstone is not None
    assert tombstone.auth_user_deleted_at is None


def test_delete_me_is_idempotent(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
    smartcar: FakeSmartcar,
    supabase: FakeSupabase,
) -> None:
    seed_account()
    assert api.delete(ME, headers=auth()).status_code == 204
    smartcar_calls, supabase_calls = len(smartcar.requests), len(supabase.requests)

    assert api.delete(ME, headers=auth()).status_code == 204

    # Nothing left to revoke, and the auth user is already gone.
    assert len(smartcar.requests) == smartcar_calls
    assert len(supabase.requests) == supabase_calls
    assert db.scalar(select(func.count()).select_from(AccountDeletion)) == 1


def test_a_second_call_retries_an_owed_auth_deletion(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    supabase: FakeSupabase,
) -> None:
    supabase.status = 503
    assert api.delete(ME, headers=auth()).status_code == 204
    supabase.status = 200
    assert api.delete(ME, headers=auth()).status_code == 204
    assert supabase.deleted_users == [str(TEST_USER_ID)] * 2
    tombstone = _tombstone(db)
    assert tombstone is not None
    assert tombstone.auth_user_deleted_at is not None


def test_a_user_who_never_opened_the_app_can_still_delete(
    api: TestClient, db: Session, auth: Callable[..., dict[str, str]], supabase: FakeSupabase
) -> None:
    assert api.delete(ME, headers=auth()).status_code == 204
    assert supabase.deleted_users == [str(TEST_USER_ID)]
    assert _tombstone(db) is not None


# ---------------------------------------------------------------------------
# Nothing comes back after deletion
# ---------------------------------------------------------------------------


def test_a_still_valid_token_cannot_recreate_the_account(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()
    assert api.delete(ME, headers=auth()).status_code == 204

    for method, path, body in [
        ("GET", ME, None),
        ("PATCH", f"{ME}/preferences", {"units": "metric"}),
        ("POST", f"{ME}/consents", {"kind": "privacy_notice", "version": "x", "granted": True}),
        ("PUT", f"/api/v1/stations/{seeded.station_id}/favorite", None),
    ]:
        response = api.request(method, path, headers=auth(), json=body)
        assert response.status_code in (401, 422), (method, path, response.status_code)
        if response.status_code == 401:
            assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    # POST /me/consents with the current version reaches the profile check too.
    response = api.post(
        f"{ME}/consents",
        headers=auth(),
        json={"kind": "privacy_notice", "version": "2026-11-19", "granted": True},
    )
    assert response.status_code == 401
    assert db.scalar(select(func.count()).select_from(Profile)) == 0


def test_other_users_are_unaffected_by_a_tombstone(
    api: TestClient, auth: Callable[..., dict[str, str]]
) -> None:
    assert api.delete(ME, headers=auth()).status_code == 204
    response = api.get(ME, headers=auth(OTHER_USER_ID))
    assert response.status_code == 200
    assert response.json()["id"] == str(OTHER_USER_ID)


def test_webhook_for_a_deleted_vehicle_is_acknowledged_and_dropped(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()
    # While the account exists, the same delivery is stored.
    stored = _post_webhook(api, _webhook_for(seeded, "evt-before"))
    assert stored.json() == {"accepted": True, "event_id": "evt-before"}

    assert api.delete(ME, headers=auth()).status_code == 204

    response = _post_webhook(api, _webhook_for(seeded, "evt-after"))
    assert response.status_code == 200
    assert response.json() == {"accepted": False, "event_id": "evt-after"}
    assert db.scalar(select(func.count()).select_from(VehicleTelemetry)) == 0
    assert db.scalar(select(func.count()).select_from(Vehicle)) == 0


def test_refresh_ingest_for_a_deleted_vehicle_is_not_accepted(
    api: TestClient,
    db: Session,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()
    assert api.delete(ME, headers=auth()).status_code == 204

    result = ingest_polled_signals(
        db,
        vehicle_id=seeded.vehicle_id,
        signals=SignalsResponse.model_validate(fixture_json("signals_bz.json")),
    )

    assert result.accepted is False
    assert result.reason == "unknown_vehicle"
    assert db.scalar(select(func.count()).select_from(VehicleTelemetry)) == 0


def test_post_ingest_hook_for_a_deleted_vehicle_writes_nothing(
    api: TestClient,
    db: Session,
    db_connection: Connection,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hook queued by a delivery just before deletion runs after it."""

    seeded = seed_account()
    assert api.delete(ME, headers=auth()).status_code == 204

    @contextmanager
    def opener() -> Iterator[Session]:
        session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    event = TelemetryIngested(
        vehicle_id=seeded.vehicle_id,
        user_id=seeded.user_id,
        signal_codes=frozenset({"odometer", "soc"}),
        earliest_recorded_at=BASE_TIME,
        is_simulated=False,
    )
    caplog.set_level(logging.ERROR)
    run_ingest_hooks(event, opener, (on_telemetry_ingested,))

    assert db.scalar(select(func.count()).select_from(ObservedTrip)) == 0
    assert any(r.getMessage() == "ingest_hook_failed" for r in caplog.records)


def test_deletion_waits_for_an_in_flight_ingest_lock(
    db_engine: Engine,
    smartcar_client: SmartcarClient,
    supabase_client: SupabaseAdminClient,
) -> None:
    """Deletion takes the vehicle row lock the webhook ingestor holds.

    Uses committed rows on two real connections (not the per-test rollback),
    cleaned up at the end.
    """

    user_id = uuid.uuid4()
    with Session(db_engine) as setup:
        setup.add(Profile(id=user_id))
        setup.flush()
        setup.add(
            Vehicle(
                user_id=user_id,
                smartcar_vehicle_id=f"sc-lock-{user_id}",
                smartcar_user_id="sc-lock-user",
                make="Toyota",
                model="bZ",
                year=2026,
                connection_status="connected",
                supported_signals=[],
            )
        )
        setup.commit()
    try:
        with Session(db_engine) as ingest, Session(db_engine) as deleter:
            # The webhook ingestor's lock (telemetry_service.ingest_vehicle_event).
            ingest.execute(
                select(Vehicle).where(Vehicle.user_id == user_id).with_for_update()
            ).all()
            deleter.execute(text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                delete_account(deleter, smartcar_client, supabase_client, user_id=user_id)
            deleter.rollback()
            ingest.rollback()

            # Once the delivery finished, deletion goes through.
            delete_account(deleter, smartcar_client, supabase_client, user_id=user_id)
            assert deleter.get(Profile, user_id) is None
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(text("DELETE FROM profiles WHERE id = :u"), {"u": user_id})
            cleanup.execute(
                text("DELETE FROM account_deletions WHERE user_id = :u"), {"u": user_id}
            )
            cleanup.commit()


def test_logs_carry_no_user_or_smartcar_ids(
    api: TestClient,
    auth: Callable[..., dict[str, str]],
    seed_account: Callable[..., SeededAccount],
    smartcar: FakeSmartcar,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seeded = seed_account()
    smartcar.delete_status = 500  # the failure path logs too
    caplog.set_level(logging.DEBUG)
    assert api.delete(ME, headers=auth()).status_code == 204
    secrets = [
        str(TEST_USER_ID),
        seeded.connection_id,
        seeded.smartcar_vehicle_id,
        seeded.smartcar_user_id,
        "test-service-role-key",
    ]
    for record in caplog.records:
        rendered = f"{record.getMessage()} {record.__dict__}"
        for secret in secrets:
            assert secret not in rendered, record.getMessage()


def test_tombstone_has_no_link_to_the_deleted_profile(db_connection: Connection) -> None:
    fks = _foreign_keys(db_connection)
    assert [fk for fk in fks if fk[0] == "account_deletions"] == []


def test_delete_account_records_the_request_time(
    db: Session,
    smartcar_client: SmartcarClient,
    supabase_client: SupabaseAdminClient,
    settings: Settings,
) -> None:
    at = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    delete_account(db, smartcar_client, supabase_client, user_id=TEST_USER_ID, now=at)
    tombstone = _tombstone(db)
    assert tombstone is not None
    assert tombstone.requested_at == at
    assert tombstone.auth_user_deleted_at is not None
    assert tombstone.auth_user_deleted_at - datetime.now(UTC) < timedelta(minutes=1)
