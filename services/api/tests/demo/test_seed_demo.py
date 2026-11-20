"""``scripts/seed_demo.py`` (P6-F2, database-design.md §5).

The seed writes only simulated rows, builds its legs, plan match and
backtests through the real pipeline, replaces the demo account on a re-run,
and refuses to touch a database that holds real data.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import make_url, select, text
from sqlalchemy.orm import Session

from app.modules.trips.models import PlannedTrip
from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.users.models import Profile, UserPreferences
from app.modules.users.privacy_models import AccountDeletion
from app.modules.vehicles.models import Vehicle, VehicleTelemetry
from tests.demo.conftest import DEMO_USER_ID, SEED_NOW, load_seed_module, make_settings
from tests.demo.test_access import _add_real_vehicle

seed_demo = load_seed_module()
DEMO_SETTINGS = make_settings(app_public_demo_mode=True)


def _seed(db: Session, **kwargs: Any) -> Any:
    summary = seed_demo.seed(
        db, user_id=DEMO_USER_ID, settings=DEMO_SETTINGS, now=kwargs.pop("now", SEED_NOW)
    )
    db.flush()
    return summary


def test_seed_writes_only_simulated_rows(db_session: Session) -> None:
    summary = _seed(db_session)

    for table in ("vehicles", "vehicle_telemetry", "planned_trips", "observed_trips"):
        real = db_session.execute(
            text(f"SELECT count(*) FROM {table} WHERE NOT is_simulated")
        ).scalar_one()
        assert real == 0, table
    assert (
        db_session.execute(
            text("SELECT count(*) FROM prediction_results WHERE NOT is_simulated")
        ).scalar_one()
        == 0
    )

    vehicle = db_session.get(Vehicle, summary.vehicle_id)
    assert vehicle is not None and vehicle.is_simulated
    assert vehicle.smartcar_vehicle_id is None and vehicle.smartcar_user_id is None
    assert vehicle.latest_state is not None
    assert set(vehicle.latest_state["signals"]) == {"soc", "range", "odometer", "charging_status"}

    sources = (
        db_session.execute(
            select(VehicleTelemetry.source)
            .where(VehicleTelemetry.vehicle_id == vehicle.id)
            .distinct()
        )
        .scalars()
        .all()
    )
    assert sources == ["simulated"]
    assert summary.telemetry_rows > 2000
    newest = db_session.execute(
        select(VehicleTelemetry.recorded_at)
        .where(VehicleTelemetry.vehicle_id == vehicle.id)
        .order_by(VehicleTelemetry.recorded_at.desc())
        .limit(1)
    ).scalar_one()
    assert newest <= SEED_NOW

    prefs = db_session.get(UserPreferences, DEMO_USER_ID)
    assert prefs is not None and prefs.primary_vehicle_id == vehicle.id


def test_seed_plan_is_matched_through_the_real_pipeline(db_session: Session) -> None:
    summary = _seed(db_session)

    trip = db_session.get(PlannedTrip, summary.trip_id)
    assert trip is not None
    assert trip.plan_status == "feasible" and trip.saved_at is not None and trip.is_simulated
    assert trip.origin["label"] == "Frederick, MD" and trip.destination["label"] == "Toronto, ON"
    stops = trip.plan_payload["stops"]
    assert len(stops) >= 2

    chain = (
        db_session.execute(select(ObservedTrip).where(ObservedTrip.planned_trip_id == trip.id))
        .scalars()
        .all()
    )
    # One leg per charging stop, plus the last one.
    assert len(chain) == len(stops) + 1
    assert summary.plans_matched == 1

    kinds = (
        db_session.execute(
            select(PredictionResult.kind).where(PredictionResult.vehicle_id == summary.vehicle_id)
        )
        .scalars()
        .all()
    )
    assert kinds.count("plan") == 1
    assert kinds.count("leg") == summary.legs_created

    flags = set(
        db_session.execute(
            select(ObservedTrip.quality_flag).where(ObservedTrip.vehicle_id == summary.vehicle_id)
        ).scalars()
    )
    assert flags == {"high", "medium", "low"}  # the demo shows every quality badge


def test_seed_is_repeatable_and_replaces_the_account(db_session: Session) -> None:
    first = _seed(db_session)
    db_session.add(AccountDeletion(user_id=DEMO_USER_ID))
    db_session.flush()
    second = _seed(db_session)

    assert db_session.get(Vehicle, first.vehicle_id) is None
    vehicles = (
        db_session.execute(select(Vehicle).where(Vehicle.user_id == DEMO_USER_ID)).scalars().all()
    )
    assert [v.id for v in vehicles] == [second.vehicle_id]
    assert (second.telemetry_rows, second.legs_created) == (
        first.telemetry_rows,
        first.legs_created,
    )
    assert db_session.get(AccountDeletion, DEMO_USER_ID) is None
    profile = db_session.get(Profile, DEMO_USER_ID)
    assert profile is not None and profile.display_name == "Demo Driver"


def test_seed_refuses_a_database_with_real_data(db_session: Session) -> None:
    _add_real_vehicle(db_session)
    with pytest.raises(seed_demo.SeedRefused, match="real"):
        _seed(db_session)
    assert db_session.get(Profile, DEMO_USER_ID) is None


def test_seed_refuses_outside_demo_mode(db_session: Session) -> None:
    with pytest.raises(seed_demo.SeedRefused, match="APP_PUBLIC_DEMO_MODE"):
        seed_demo.seed(db_session, user_id=DEMO_USER_ID, settings=make_settings(), now=SEED_NOW)


def test_target_must_name_the_database() -> None:
    url = make_url("postgresql+psycopg://u:p@db.example.com:5432/demo")
    seed_demo.check_target(url, "db.example.com/demo")
    for wrong in ("db.example.com/postgres", "localhost/demo", "demo"):
        with pytest.raises(seed_demo.SeedRefused, match="--target"):
            seed_demo.check_target(url, wrong)


def test_cli_refuses_before_connecting(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--user-id", str(DEMO_USER_ID), "--target", "localhost/chargewise_demo"]
    live = make_settings(database_url="postgresql+psycopg://u:p@localhost:1/chargewise_demo")
    assert seed_demo.main(args, settings_factory=lambda: live) == 2
    assert "APP_PUBLIC_DEMO_MODE" in capsys.readouterr().err

    demo = make_settings(
        app_public_demo_mode=True,
        database_url="postgresql+psycopg://u:p@hosted.example.com:5432/postgres",
    )
    assert seed_demo.main(args, settings_factory=lambda: demo) == 2
    assert "--target" in capsys.readouterr().err
