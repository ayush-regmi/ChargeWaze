"""Schema, cascades and ``create_planned_trip`` (phase4_plan.md F1)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.stations.models import ChargingStation
from app.modules.trips.models import PlannedStop, PlannedTrip
from app.modules.trips.service import NewPlannedStop, create_planned_trip
from app.modules.users.models import Profile
from app.modules.vehicles.models import Vehicle
from tests.trips.conftest import BASE_TIME, DESTINATION, FEASIBLE_PAYLOAD, ORIGIN


def test_trip_tables_have_rls_enabled(db_connection: Connection) -> None:
    rows = db_connection.execute(
        text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace "
            "AND relname IN ('planned_trips', 'planned_stops') ORDER BY relname"
        )
    ).all()
    assert [(r.relname, r.relrowsecurity) for r in rows] == [
        ("planned_stops", True),
        ("planned_trips", True),
    ]


def test_trip_models_match_migrated_schema(db_connection: Connection) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import app.modules.trips.models  # noqa: F401
    from app.db.session import Base

    context = MigrationContext.configure(
        db_connection, opts={"compare_type": True, "compare_server_default": False}
    )
    diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"Schema drift between models and migrations: {diff}"


def test_create_planned_trip_stores_stops_in_order_and_payload_unchanged(
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_station: Callable[..., ChargingStation],
) -> None:
    vehicle = add_vehicle()
    s1, s2 = add_station("First"), add_station("Second")
    trip_id = uuid.uuid4()
    payload: dict[str, Any] = {
        **FEASIBLE_PAYLOAD,
        "trip_id": str(trip_id),
        "stops": [{"sequence": 1, "station": {"id": str(s1.id)}}, {"sequence": 2}],
        "nested": {"list": [1, 2.5, None, "x"], "flag": True},
    }
    # The third stop's station was evicted (station_id None) — still stored in order.
    stops = [
        NewPlannedStop(1, s1.id, arrival_soc=0.162, departure_soc=0.61, charging_minutes=15),
        NewPlannedStop(2, s2.id, arrival_soc=0.2, departure_soc=0.8, charging_minutes=22),
        NewPlannedStop(3, None, arrival_soc=0.15, departure_soc=0.5, charging_minutes=8),
    ]
    create_planned_trip(
        db,
        trip_id=trip_id,
        created_at=BASE_TIME,
        user_id=vehicle.user_id,
        vehicle_id=vehicle.id,
        origin=ORIGIN,
        destination=DESTINATION,
        departure_time=BASE_TIME + timedelta(hours=14),
        plan_status="feasible",
        plan_payload=payload,
        model_version="deterministic-v1",
        stops=stops,
    )
    db.commit()
    db.expire_all()

    trip = db.get(PlannedTrip, trip_id)
    assert trip is not None
    assert trip.plan_payload == payload
    assert trip.created_at == BASE_TIME
    assert trip.saved_at is None
    assert trip.status == "planned"
    assert trip.is_simulated is False
    assert trip.origin == {"lat": 39.4143, "lon": -77.4105, "label": "Frederick, MD"}
    assert trip.departure_time == BASE_TIME + timedelta(hours=14)
    assert [
        (s.sequence, s.station_id, s.arrival_soc, s.departure_soc, s.charging_minutes)
        for s in trip.stops
    ] == [
        (1, s1.id, Decimal("0.162"), Decimal("0.610"), 15),
        (2, s2.id, Decimal("0.200"), Decimal("0.800"), 22),
        (3, None, Decimal("0.150"), Decimal("0.500"), 8),
    ]


def test_create_planned_trip_rejects_out_of_order_sequences(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    with pytest.raises(ValueError, match=r"1\.\.n"):
        create_planned_trip(
            db,
            trip_id=uuid.uuid4(),
            created_at=BASE_TIME,
            user_id=vehicle.user_id,
            vehicle_id=vehicle.id,
            origin=ORIGIN,
            destination=DESTINATION,
            departure_time=None,
            plan_status="feasible",
            plan_payload=FEASIBLE_PAYLOAD,
            model_version="deterministic-v1",
            stops=[NewPlannedStop(2, None, 0.2, 0.8, 10), NewPlannedStop(1, None, 0.2, 0.8, 10)],
        )


def _stop_count(db: Session, trip_id: uuid.UUID) -> int:
    return len(db.scalars(select(PlannedStop).where(PlannedStop.trip_id == trip_id)).all())


def test_profile_delete_cascades_to_trips_and_stops(
    db: Session, add_vehicle: Callable[..., Vehicle], add_trip: Callable[..., uuid.UUID]
) -> None:
    vehicle = add_vehicle()
    tid = add_trip(vehicle, stops=[NewPlannedStop(1, None, 0.2, 0.8, 10)])
    db.execute(delete(Profile).where(Profile.id == vehicle.user_id))
    db.commit()
    db.expire_all()
    assert db.get(PlannedTrip, tid) is None
    assert _stop_count(db, tid) == 0


def test_vehicle_delete_cascades_to_trips(
    db: Session, add_vehicle: Callable[..., Vehicle], add_trip: Callable[..., uuid.UUID]
) -> None:
    vehicle = add_vehicle()
    tid = add_trip(vehicle, stops=[NewPlannedStop(1, None, 0.2, 0.8, 10)])
    db.execute(delete(Vehicle).where(Vehicle.id == vehicle.id))
    db.commit()
    db.expire_all()
    assert db.get(PlannedTrip, tid) is None
    assert _stop_count(db, tid) == 0


def test_trip_delete_cascades_to_stops(
    db: Session, add_vehicle: Callable[..., Vehicle], add_trip: Callable[..., uuid.UUID]
) -> None:
    vehicle = add_vehicle()
    tid = add_trip(vehicle, stops=[NewPlannedStop(1, None, 0.2, 0.8, 10)])
    db.execute(delete(PlannedTrip).where(PlannedTrip.id == tid))
    db.commit()
    assert _stop_count(db, tid) == 0


def test_station_delete_sets_stop_station_null(
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_station: Callable[..., ChargingStation],
    add_trip: Callable[..., uuid.UUID],
) -> None:
    vehicle = add_vehicle()
    station = add_station()
    tid = add_trip(vehicle, stops=[NewPlannedStop(1, station.id, 0.2, 0.8, 10)])
    db.execute(delete(ChargingStation).where(ChargingStation.id == station.id))
    db.commit()
    db.expire_all()
    stops = db.scalars(select(PlannedStop).where(PlannedStop.trip_id == tid)).all()
    assert [(s.sequence, s.station_id) for s in stops] == [(1, None)]


@pytest.mark.parametrize(
    ("column", "value"),
    [("arrival_soc", "1.5"), ("departure_soc", "-0.1"), ("charging_minutes", "-1")],
)
def test_stop_checks_reject_out_of_range_values(
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_trip: Callable[..., uuid.UUID],
    column: str,
    value: str,
) -> None:
    vehicle = add_vehicle()
    tid = add_trip(vehicle, stops=[NewPlannedStop(1, None, 0.2, 0.8, 10)])
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text(f"UPDATE planned_stops SET {column} = :v WHERE trip_id = :t"),
            {"v": Decimal(value), "t": tid},
        )


def test_plan_status_check_rejects_unknown_value(
    db: Session, add_vehicle: Callable[..., Vehicle], add_trip: Callable[..., uuid.UUID]
) -> None:
    vehicle = add_vehicle()
    tid = add_trip(vehicle)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text("UPDATE planned_trips SET plan_status = 'maybe' WHERE id = :t"), {"t": tid})
