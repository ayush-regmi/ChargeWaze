"""``scripts/retention.py`` (P6-F5): what it deletes, what it keeps, and the
account-deletion backlog it works through."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.integrations.smartcar.client import SmartcarClient
from app.integrations.supabase.admin import SupabaseAdminClient
from app.modules.trips.models import PlannedStop, PlannedTrip
from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.trips.service import TripEndpoint, create_planned_trip
from app.modules.users.privacy_models import AccountDeletion
from app.modules.vehicles.models import Vehicle, VehicleRefresh, VehicleTelemetry
from tests.conftest import TEST_USER_ID
from tests.privacy.conftest import FakeSmartcar, FakeSupabase, SeededAccount

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "retention.py"


@pytest.fixture(scope="module")
def retention() -> ModuleType:
    spec = importlib.util.spec_from_file_location("retention_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["retention_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def run(
    retention: ModuleType,
    db: Session,
    settings: Settings,
    smartcar: FakeSmartcar,
    supabase: FakeSupabase,
) -> Callable[..., dict[str, int]]:
    # The script closes its clients after each run, so each run gets new ones
    # over the same fakes.
    def smartcar_factory() -> SmartcarClient:
        return SmartcarClient.from_settings(
            settings, transport=httpx.MockTransport(smartcar.handler)
        )

    def supabase_factory() -> SupabaseAdminClient:
        return SupabaseAdminClient.from_settings(
            settings, transport=httpx.MockTransport(supabase.handler)
        )

    def _run(*, dry_run: bool = False, now: datetime = NOW, **kwargs: Any) -> dict[str, int]:
        result: dict[str, int] = retention.run(
            db,
            settings=settings,
            now=now,
            dry_run=dry_run,
            smartcar_factory=smartcar_factory,
            supabase_factory=supabase_factory,
            **kwargs,
        )
        db.expire_all()
        return result

    return _run


def _plan(
    db: Session,
    vehicle_id: uuid.UUID,
    *,
    age_days: float,
    saved: bool = False,
    status: str = "planned",
) -> uuid.UUID:
    trip_id = uuid.uuid4()
    create_planned_trip(
        db,
        trip_id=trip_id,
        created_at=NOW - timedelta(days=age_days),
        user_id=TEST_USER_ID,
        vehicle_id=vehicle_id,
        origin=TripEndpoint(lat=39.41, lon=-77.41, label="A"),
        destination=TripEndpoint(lat=43.65, lon=-79.38, label="B"),
        departure_time=None,
        plan_status="feasible",
        plan_payload={"status": "feasible"},
        model_version="deterministic-v1",
        stops=[],
    )
    trip = db.get(PlannedTrip, trip_id)
    assert trip is not None
    trip.status = status
    if saved:
        trip.saved_at = trip.created_at
    db.commit()
    return trip_id


def _reading(vehicle_id: uuid.UUID, *, age_days: float, n: int) -> VehicleTelemetry:
    return VehicleTelemetry(
        vehicle_id=vehicle_id,
        recorded_at=NOW - timedelta(days=age_days),
        source="smartcar",
        source_event_id=f"evt-{vehicle_id}-{age_days}-{n}",
        signal_code="odometer",
        value=Decimal(n),
        unit="km",
        raw_payload={},
    )


def _trip_ids(db: Session) -> set[uuid.UUID]:
    return set(db.scalars(select(PlannedTrip.id)).all())


# ---------------------------------------------------------------------------
# Unsaved plans
# ---------------------------------------------------------------------------


def test_only_old_unsaved_unmatched_plans_expire(
    run: Callable[..., dict[str, int]],
    db: Session,
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()  # its plan is from 2026-11-06: 13 days old, kept
    expired = _plan(db, seeded.vehicle_id, age_days=31)
    recent = _plan(db, seeded.vehicle_id, age_days=29)
    saved = _plan(db, seeded.vehicle_id, age_days=400, saved=True)
    matched = _plan(db, seeded.vehicle_id, age_days=400, status="completed")

    counts = run()

    assert counts["unsaved_plans"] == 1
    assert _trip_ids(db) == {seeded.trip_id, recent, saved, matched}
    assert expired not in _trip_ids(db)


def test_an_expired_plan_takes_its_stops_and_plan_prediction_but_keeps_legs(
    run: Callable[..., dict[str, int]],
    db: Session,
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()
    # Age the seeded plan (it has a stop, a matched leg and a 'plan' row).
    trip = db.get(PlannedTrip, seeded.trip_id)
    assert trip is not None
    trip.created_at = NOW - timedelta(days=60)
    db.commit()

    assert run()["unsaved_plans"] == 1

    assert db.scalar(select(func.count()).select_from(PlannedStop)) == 0
    kinds = db.scalars(select(PredictionResult.kind)).all()
    assert kinds == ["leg"]
    leg = db.get(ObservedTrip, seeded.leg_id)
    assert leg is not None
    assert leg.planned_trip_id is None


# ---------------------------------------------------------------------------
# Telemetry and refresh log
# ---------------------------------------------------------------------------


def test_telemetry_older_than_retention_is_deleted_in_batches(
    run: Callable[..., dict[str, int]],
    db: Session,
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()  # one reading from 2026-11-06, kept
    db.add_all([_reading(seeded.vehicle_id, age_days=731, n=n) for n in range(5)])
    db.add(_reading(seeded.simulated_vehicle_id, age_days=800, n=0))
    db.add(_reading(seeded.vehicle_id, age_days=729, n=0))
    db.commit()

    counts = run(telemetry_batch_size=2)

    assert counts["telemetry"] == 6
    ages = sorted(
        (NOW - recorded_at).days for recorded_at in db.scalars(select(VehicleTelemetry.recorded_at))
    )
    assert ages == [12, 729]


def test_old_refresh_log_rows_are_deleted(
    run: Callable[..., dict[str, int]],
    db: Session,
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()  # its refresh is 13 days old, kept
    db.add(
        VehicleRefresh(
            vehicle_id=seeded.vehicle_id, requested_at=NOW - timedelta(days=31), outcome="ok"
        )
    )
    db.commit()

    assert run()["refreshes"] == 1
    assert db.scalar(select(func.count()).select_from(VehicleRefresh)) == 1


# ---------------------------------------------------------------------------
# Account-deletion backlog
# ---------------------------------------------------------------------------


def _tombstone(
    db: Session,
    *,
    age_days: float,
    pending: list[str] | None = None,
    auth_deleted: bool = True,
) -> uuid.UUID:
    user_id = uuid.uuid4()
    requested = NOW - timedelta(days=age_days)
    db.add(
        AccountDeletion(
            user_id=user_id,
            requested_at=requested,
            auth_user_deleted_at=requested if auth_deleted else None,
            pending_smartcar_connection_ids=pending or [],
        )
    )
    db.commit()
    return user_id


def _row(db: Session, user_id: uuid.UUID) -> AccountDeletion | None:
    return db.get(AccountDeletion, user_id)


def test_owed_revocations_are_retried(
    run: Callable[..., dict[str, int]], db: Session, smartcar: FakeSmartcar
) -> None:
    user_id = _tombstone(db, age_days=1, pending=["sc-ok", "sc-still-down"])
    smartcar.status_for = {"sc-still-down": 503}

    counts = run()

    assert smartcar.deleted_connections == ["sc-ok", "sc-still-down"]
    assert counts["smartcar_revoked"] == 1
    assert counts["smartcar_pending"] == 1
    row = _row(db, user_id)
    assert row is not None
    assert row.pending_smartcar_connection_ids == ["sc-still-down"]
    assert row.attempts == 1
    assert row.last_attempt_at == NOW


def test_a_connection_now_used_by_a_stored_vehicle_is_never_revoked(
    run: Callable[..., dict[str, int]],
    db: Session,
    smartcar: FakeSmartcar,
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()
    user_id = _tombstone(db, age_days=1, pending=[seeded.connection_id])

    run()

    assert smartcar.deleted_connections == []
    row = _row(db, user_id)
    assert row is not None
    assert row.pending_smartcar_connection_ids == []
    vehicle = db.get(Vehicle, seeded.vehicle_id)
    assert vehicle is not None and vehicle.smartcar_connection_id == seeded.connection_id


def test_revocations_are_abandoned_after_the_give_up_window(
    run: Callable[..., dict[str, int]], db: Session, smartcar: FakeSmartcar
) -> None:
    user_id = _tombstone(db, age_days=31, pending=["sc-gone"])

    counts = run()

    assert smartcar.deleted_connections == []
    assert counts["smartcar_abandoned"] == 1
    # Nothing owed and older than the tombstone window: removed.
    assert counts["tombstones_removed"] == 1
    assert _row(db, user_id) is None


def test_owed_auth_deletions_are_retried(
    run: Callable[..., dict[str, int]], db: Session, supabase: FakeSupabase
) -> None:
    done = _tombstone(db, age_days=1, auth_deleted=False)
    supabase_calls_before = len(supabase.requests)

    counts = run()

    assert len(supabase.requests) == supabase_calls_before + 1
    assert counts["auth_users_deleted"] == 1
    row = _row(db, done)
    assert row is not None
    assert row.auth_user_deleted_at == NOW


def test_failed_auth_deletion_keeps_the_tombstone_however_old(
    run: Callable[..., dict[str, int]], db: Session, supabase: FakeSupabase
) -> None:
    supabase.status = 500
    user_id = _tombstone(db, age_days=90, auth_deleted=False)

    counts = run()

    assert counts["auth_users_pending"] == 1
    assert counts["tombstones_removed"] == 0
    row = _row(db, user_id)
    assert row is not None
    assert row.auth_user_deleted_at is None


def test_a_late_auth_deletion_keeps_the_tombstone_for_a_full_window(
    run: Callable[..., dict[str, int]], db: Session, supabase: FakeSupabase
) -> None:
    """Regression (PR #69 review): age counts from the auth-user deletion.

    The auth deletion failed for longer than the tombstone window, so the
    user could keep refreshing tokens. The run that finally deletes the auth
    user must keep the tombstone: a token issued just before is still valid.
    """

    supabase.status = 503
    user_id = _tombstone(db, age_days=30, auth_deleted=False)
    run(now=NOW - timedelta(days=1))
    row = _row(db, user_id)
    assert row is not None and row.auth_user_deleted_at is None

    supabase.status = 200
    counts = run()
    assert counts["auth_users_deleted"] == 1
    assert counts["tombstones_removed"] == 0
    row = _row(db, user_id)
    assert row is not None and row.auth_user_deleted_at == NOW

    # Still inside the window: kept.
    assert run(now=NOW + timedelta(days=6))["tombstones_removed"] == 0
    assert _row(db, user_id) is not None
    # One window after the auth deletion: removed.
    assert run(now=NOW + timedelta(days=7, minutes=1))["tombstones_removed"] == 1
    assert _row(db, user_id) is None


def test_tombstone_age_ignores_the_request_time(
    run: Callable[..., dict[str, int]], db: Session
) -> None:
    user_id = uuid.uuid4()
    db.add(
        AccountDeletion(
            user_id=user_id,
            requested_at=NOW - timedelta(days=60),
            auth_user_deleted_at=NOW - timedelta(days=3),
        )
    )
    db.commit()
    assert run()["tombstones_removed"] == 0
    assert _row(db, user_id) is not None


def test_finished_tombstones_are_removed_only_after_the_window(
    run: Callable[..., dict[str, int]],
    db: Session,
    smartcar: FakeSmartcar,
    supabase: FakeSupabase,
) -> None:
    old = _tombstone(db, age_days=8)
    fresh = _tombstone(db, age_days=6)

    counts = run()

    assert counts["tombstones_removed"] == 1
    assert _row(db, old) is None
    assert _row(db, fresh) is not None
    # Nothing was owed, so nothing was called.
    assert smartcar.requests == [] and supabase.requests == []


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def test_dry_run_counts_and_changes_nothing(
    run: Callable[..., dict[str, int]],
    db: Session,
    smartcar: FakeSmartcar,
    supabase: FakeSupabase,
    seed_account: Callable[..., SeededAccount],
) -> None:
    seeded = seed_account()
    _plan(db, seeded.vehicle_id, age_days=31)
    db.add(_reading(seeded.vehicle_id, age_days=731, n=0))
    db.add(
        VehicleRefresh(
            vehicle_id=seeded.vehicle_id, requested_at=NOW - timedelta(days=31), outcome="ok"
        )
    )
    db.commit()
    _tombstone(db, age_days=1, pending=["sc-x"])
    _tombstone(db, age_days=1, auth_deleted=False)
    _tombstone(db, age_days=30)

    counts = run(dry_run=True)

    assert counts == {"unsaved_plans": 1, "telemetry": 1, "refreshes": 1, "deletions_owed": 2}
    assert smartcar.requests == [] and supabase.requests == []
    assert run(dry_run=True) == counts
