"""Constraints and indexes on the vehicles tables (database-design.md §3.3-3.5)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.users.models import Profile, UserPreferences
from app.modules.vehicles.models import Vehicle, VehicleSpecs, VehicleTelemetry

RECORDED_AT = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session(db_connection: Connection) -> Iterator[Session]:
    s = Session(bind=db_connection, join_transaction_mode="create_savepoint", autoflush=False)
    try:
        yield s
    finally:
        s.close()


def _profile(session: Session) -> uuid.UUID:
    user_id = uuid.uuid4()
    session.add(Profile(id=user_id))
    session.flush()
    return user_id


def _vehicle(session: Session, user_id: uuid.UUID, **overrides: Any) -> Vehicle:
    fields: dict[str, Any] = {
        "user_id": user_id,
        "smartcar_vehicle_id": f"sc-{uuid.uuid4()}",
        "smartcar_user_id": "sc-user",
        "make": "TOYOTA",
        "model": "bZ",
        "year": 2026,
    }
    fields.update(overrides)
    vehicle = Vehicle(**fields)
    session.add(vehicle)
    session.flush()
    return vehicle


def _telemetry(vehicle_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "vehicle_id": vehicle_id,
        "recorded_at": RECORDED_AT,
        "source": "smartcar",
        "source_event_id": "evt-1",
        "signal_code": "soc",
        "value": Decimal("81"),
        "unit": "percent",
        "raw_payload": {"code": "soc", "value": 81},
    }
    row.update(overrides)
    return row


def _assert_rejected(session: Session, obj: object) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(obj)
        session.flush()


def test_vehicle_defaults(session: Session) -> None:
    vehicle = _vehicle(session, _profile(session))
    session.refresh(vehicle)

    assert isinstance(vehicle.id, uuid.UUID)
    assert vehicle.connection_status == "pending"
    assert vehicle.supported_signals == []
    assert vehicle.latest_state is None
    assert vehicle.is_simulated is False


def test_same_real_vehicle_cannot_be_connected_twice_by_one_user(session: Session) -> None:
    user_id = _profile(session)
    _vehicle(session, user_id, smartcar_vehicle_id="sc-bz")

    _assert_rejected(
        session,
        Vehicle(user_id=user_id, smartcar_vehicle_id="sc-bz", make="TOYOTA", model="bZ", year=2026),
    )


def test_unique_connect_is_per_user_and_ignores_simulated(session: Session) -> None:
    first, second = _profile(session), _profile(session)
    _vehicle(session, first, smartcar_vehicle_id="sc-bz")
    _vehicle(session, second, smartcar_vehicle_id="sc-bz")
    # Simulated vehicles have no Smartcar ID; the partial index lets several coexist.
    _vehicle(session, first, smartcar_vehicle_id=None, is_simulated=True)
    _vehicle(session, first, smartcar_vehicle_id=None, is_simulated=True)


def test_connection_status_must_be_known(session: Session) -> None:
    user_id = _profile(session)
    for status in ("connected", "disconnected", "pending"):
        _vehicle(session, user_id, connection_status=status)

    _assert_rejected(
        session,
        Vehicle(user_id=user_id, make="TOYOTA", model="bZ", year=2026, connection_status="active"),
    )


def test_usable_capacity_must_be_positive(session: Session) -> None:
    vehicle = _vehicle(session, _profile(session))

    _assert_rejected(session, VehicleSpecs(vehicle_id=vehicle.id, usable_capacity_kwh=Decimal("0")))

    specs = VehicleSpecs(vehicle_id=vehicle.id)  # unknown capacity stays NULL (D8)
    session.add(specs)
    session.flush()
    session.refresh(specs)
    assert specs.usable_capacity_kwh is None
    assert specs.connectors == []
    assert specs.charging_curve == []


def test_telemetry_source_must_be_known(session: Session) -> None:
    vehicle = _vehicle(session, _profile(session))

    _assert_rejected(session, VehicleTelemetry(**_telemetry(vehicle.id, source="tesla")))


def test_telemetry_dedup_index_rejects_redelivered_signal(session: Session) -> None:
    vehicle = _vehicle(session, _profile(session))
    session.add(VehicleTelemetry(**_telemetry(vehicle.id)))
    # Same event, different signal: a separate row.
    session.add(VehicleTelemetry(**_telemetry(vehicle.id, signal_code="range", unit="km")))
    # Manual readings have no event ID and are never deduplicated.
    session.add(VehicleTelemetry(**_telemetry(vehicle.id, source="manual", source_event_id=None)))
    session.add(VehicleTelemetry(**_telemetry(vehicle.id, source="manual", source_event_id=None)))
    session.flush()

    _assert_rejected(session, VehicleTelemetry(**_telemetry(vehicle.id)))


def test_telemetry_on_conflict_do_nothing_uses_dedup_index(session: Session) -> None:
    """The webhook ingestor (F5) relies on this exact upsert form."""

    vehicle = _vehicle(session, _profile(session))
    stmt = (
        pg_insert(VehicleTelemetry)
        .values(**_telemetry(vehicle.id))
        .on_conflict_do_nothing(
            index_elements=["source", "source_event_id", "signal_code"],
            index_where=text("source_event_id IS NOT NULL"),
        )
        .returning(VehicleTelemetry.id)
    )

    assert len(session.scalars(stmt).all()) == 1
    assert session.scalars(stmt).all() == []  # duplicate: nothing inserted
    count = session.scalar(
        select(func.count()).select_from(VehicleTelemetry).where(
            VehicleTelemetry.vehicle_id == vehicle.id
        )
    )
    assert count == 1


def test_deleting_profile_cascades_to_vehicle_data(session: Session) -> None:
    user_id = _profile(session)
    vehicle = _vehicle(session, user_id)
    session.add(VehicleSpecs(vehicle_id=vehicle.id, usable_capacity_kwh=Decimal("71.40")))
    session.add(VehicleTelemetry(**_telemetry(vehicle.id)))
    session.flush()
    vehicle_id = vehicle.id
    session.expunge_all()

    session.execute(text("DELETE FROM profiles WHERE id = :id"), {"id": user_id})

    for model, column in (
        (Vehicle, Vehicle.id),
        (VehicleSpecs, VehicleSpecs.vehicle_id),
        (VehicleTelemetry, VehicleTelemetry.vehicle_id),
    ):
        remaining = session.scalar(
            select(func.count()).select_from(model).where(column == vehicle_id)
        )
        assert remaining == 0, f"{model.__tablename__} rows survived profile deletion"


def test_primary_vehicle_is_cleared_when_vehicle_row_is_deleted(session: Session) -> None:
    user_id = _profile(session)
    vehicle = _vehicle(session, user_id)
    session.add(UserPreferences(user_id=user_id, primary_vehicle_id=vehicle.id))
    session.flush()
    session.expunge_all()

    session.execute(text("DELETE FROM vehicles WHERE id = :id"), {"id": vehicle.id})

    prefs = session.get(UserPreferences, user_id)
    assert prefs is not None
    assert prefs.primary_vehicle_id is None


def test_primary_vehicle_must_exist(session: Session) -> None:
    user_id = _profile(session)

    _assert_rejected(session, UserPreferences(user_id=user_id, primary_vehicle_id=uuid.uuid4()))
