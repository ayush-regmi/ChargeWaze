"""Schema for ``observed_trips`` and ``prediction_results`` (phase5_plan.md F1)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.modules.trips.models import PlannedTrip
from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.users.models import Profile
from app.modules.vehicles.models import Vehicle
from tests.trips.conftest import BASE_TIME

API_ROOT = Path(__file__).resolve().parents[2]
THIS_REVISION = "20261120_01"
PREVIOUS_REVISION = "20261119_01"
TABLES = ("observed_trips", "prediction_results")


def _alembic_config(conn: Connection | None = None) -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    if conn is not None:
        cfg.attributes["connection"] = conn
    return cfg


def _leg_values(vehicle: Vehicle, **overrides: Any) -> dict[str, Any]:
    """A valid high-quality leg: 24650.00 → 24712.40 km, SoC 80 → 70."""

    values: dict[str, Any] = {
        "user_id": vehicle.user_id,
        "vehicle_id": vehicle.id,
        "start_time": BASE_TIME,
        "start_time_latest": BASE_TIME,
        "end_time": BASE_TIME + timedelta(minutes=50),
        "odometer_start_km": Decimal("24650.00"),
        "odometer_end_km": Decimal("24712.40"),
        "distance_km": Decimal("62.40"),
        "soc_start_percent": Decimal("80"),
        "soc_start_recorded_at": BASE_TIME,
        "soc_end_percent": Decimal("70"),
        "soc_end_recorded_at": BASE_TIME + timedelta(minutes=50),
        "usable_capacity_kwh": Decimal("71.00"),
        "capacity_source": "catalog",
        "energy_kwh": Decimal("7.100"),
        "energy_kwh_uncertainty": Decimal("0.710"),
        "energy_source": "soc_inferred",
        "quality_flag": "high",
        "review_status": "auto",
        "closed": True,
        "inference_version": "telemetry-v1",
    }
    values.update(overrides)
    return values


def _prediction_values(vehicle: Vehicle, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "user_id": vehicle.user_id,
        "vehicle_id": vehicle.id,
        "kind": "leg",
        "model_version": "deterministic-v1",
        "distance_km": Decimal("62.40"),
        "observed_distance_km": Decimal("62.40"),
        "predicted_energy_kwh": Decimal("10.920"),
        "observed_energy_kwh": Decimal("7.100"),
        "error_kwh": Decimal("-3.820"),
        "inputs": {
            "baseline_kwh_per_km": 0.175,
            "baseline_source": "catalog",
            "usable_capacity_kwh": 71.0,
        },
    }
    values.update(overrides)
    return values


@pytest.fixture()
def add_leg(db: Session) -> Callable[..., ObservedTrip]:
    def _add(vehicle: Vehicle, **overrides: Any) -> ObservedTrip:
        leg = ObservedTrip(**_leg_values(vehicle, **overrides))
        db.add(leg)
        db.commit()
        return leg

    return _add


@pytest.fixture()
def add_prediction(db: Session) -> Callable[..., PredictionResult]:
    def _add(vehicle: Vehicle, **overrides: Any) -> PredictionResult:
        row = PredictionResult(**_prediction_values(vehicle, **overrides))
        db.add(row)
        db.commit()
        return row

    return _add


def _violated_constraint(exc: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    return getattr(getattr(exc.value.orig, "diag", None), "constraint_name", None)


# ---- migration -------------------------------------------------------------


def test_observed_tables_have_rls_enabled(db_connection: Connection) -> None:
    rows = db_connection.execute(
        text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace "
            "AND relname IN ('observed_trips', 'prediction_results') ORDER BY relname"
        )
    ).all()
    assert [(r.relname, r.relrowsecurity) for r in rows] == [
        ("observed_trips", True),
        ("prediction_results", True),
    ]


def test_observed_models_match_migrated_schema(db_connection: Connection) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import app.modules.trips.observed_models  # noqa: F401
    from app.db.session import Base

    context = MigrationContext.configure(
        db_connection, opts={"compare_type": True, "compare_server_default": False}
    )
    diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"Schema drift between models and migrations: {diff}"


def test_migration_chain_has_one_head_and_it_is_this_revision() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_heads() == [THIS_REVISION]
    revision = script.get_revision(THIS_REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION


def _public_tables(conn: Connection) -> set[str]:
    return set(
        conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars()
    )


def test_migration_downgrades_and_upgrades_cleanly(db_connection: Connection) -> None:
    # Runs inside db_connection's outer transaction, so the rollback restores it.
    cfg = _alembic_config(db_connection)
    command.downgrade(cfg, PREVIOUS_REVISION)
    assert _public_tables(db_connection).isdisjoint(TABLES)
    command.upgrade(cfg, "head")
    assert _public_tables(db_connection).issuperset(TABLES)
    version = db_connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == THIS_REVISION


# ---- observed_trips ---------------------------------------------------------


def test_leg_round_trips_with_defaults(
    db: Session, add_vehicle: Callable[..., Vehicle], add_leg: Callable[..., ObservedTrip]
) -> None:
    vehicle = add_vehicle()
    leg = add_leg(vehicle)
    db.expire_all()

    stored = db.get(ObservedTrip, leg.id)
    assert stored is not None
    assert stored.distance_km == Decimal("62.40")
    assert stored.energy_kwh == Decimal("7.100")
    assert stored.quality_reasons == []
    assert stored.planned_trip_id is None
    assert stored.is_simulated is False
    assert stored.reviewed_at is None
    assert stored.created_at is not None and stored.updated_at is not None


def test_leg_server_defaults(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    values = _leg_values(vehicle)
    del values["closed"]
    row = db.execute(
        insert(ObservedTrip)
        .values(**values)
        .returning(ObservedTrip.closed, ObservedTrip.quality_reasons, ObservedTrip.is_simulated)
    ).one()
    assert (row.closed, row.quality_reasons, row.is_simulated) == (False, [], False)


def test_leg_without_energy_is_allowed(
    db: Session, add_vehicle: Callable[..., Vehicle], add_leg: Callable[..., ObservedTrip]
) -> None:
    vehicle = add_vehicle()
    leg = add_leg(
        vehicle,
        start_time_latest=BASE_TIME + timedelta(minutes=12),
        soc_start_percent=None,
        soc_start_recorded_at=None,
        usable_capacity_kwh=None,
        capacity_source=None,
        energy_kwh=None,
        energy_kwh_uncertainty=None,
        energy_source=None,
        quality_flag="low",
        quality_reasons=["soc_missing", "start_time_bounded"],
        review_status="needs_review",
        closed=False,
    )
    db.expire_all()
    stored = db.get(ObservedTrip, leg.id)
    assert stored is not None
    assert stored.quality_reasons == ["soc_missing", "start_time_bounded"]
    assert stored.energy_kwh is None


_BAD_LEGS: list[tuple[str, dict[str, Any]]] = [
    ("observed_trips_start_bound_order", {"start_time_latest": BASE_TIME - timedelta(minutes=1)}),
    (
        "observed_trips_end_after_start",
        {
            "start_time_latest": BASE_TIME + timedelta(minutes=10),
            "end_time": BASE_TIME + timedelta(minutes=5),
        },
    ),
    (
        "observed_trips_odometer_increases",
        {"odometer_end_km": Decimal("24650.00"), "distance_km": Decimal("0.00")},
    ),
    (
        "observed_trips_distance_positive",
        {"odometer_end_km": Decimal("24650.00"), "distance_km": Decimal("0.00")},
    ),
    ("observed_trips_distance_matches_odometer", {"distance_km": Decimal("60.00")}),
    ("observed_trips_soc_start_range", {"soc_start_percent": Decimal("100.01")}),
    ("observed_trips_soc_end_range", {"soc_end_percent": Decimal("-1")}),
    ("observed_trips_capacity_source_enum", {"capacity_source": "guess"}),
    ("observed_trips_capacity_source_iff_capacity", {"capacity_source": None}),
    ("observed_trips_capacity_source_iff_capacity", {"usable_capacity_kwh": None}),
    ("observed_trips_energy_source_enum", {"energy_source": "odometer_inferred"}),
    ("observed_trips_energy_source_iff_energy", {"energy_source": None}),
    ("observed_trips_energy_source_iff_energy", {"energy_kwh": None}),
    ("observed_trips_quality_flag_enum", {"quality_flag": "excellent"}),
    ("observed_trips_review_status_enum", {"review_status": "pending"}),
]


@pytest.mark.parametrize(
    ("constraint", "overrides"),
    _BAD_LEGS,
    ids=[f"{name}-{'-'.join(o)}" for name, o in _BAD_LEGS],
)
def test_leg_check_constraints_reject_bad_rows(
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    constraint: str,
    overrides: dict[str, Any],
) -> None:
    vehicle = add_vehicle()
    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        db.add(ObservedTrip(**_leg_values(vehicle, **overrides)))
        db.flush()
    # Two checks can fail on the same row (odometer and distance); either is enough.
    if constraint in ("observed_trips_odometer_increases", "observed_trips_distance_positive"):
        assert _violated_constraint(exc) in (
            "observed_trips_odometer_increases",
            "observed_trips_distance_positive",
        )
    else:
        assert _violated_constraint(exc) == constraint


def test_distance_check_fires_on_its_own(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    # Odometer increases, but distance is zero.
    vehicle = add_vehicle()
    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        db.add(ObservedTrip(**_leg_values(vehicle, distance_km=Decimal("0.00"))))
        db.flush()
    assert _violated_constraint(exc) in (
        "observed_trips_distance_positive",
        "observed_trips_distance_matches_odometer",
    )


@pytest.mark.parametrize(
    "column",
    [
        "user_id",
        "vehicle_id",
        "start_time",
        "start_time_latest",
        "end_time",
        "odometer_start_km",
        "odometer_end_km",
        "distance_km",
        "quality_flag",
        "review_status",
        "inference_version",
    ],
)
def test_leg_required_columns_are_not_null(
    db: Session, add_vehicle: Callable[..., Vehicle], column: str
) -> None:
    vehicle = add_vehicle()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ObservedTrip(**_leg_values(vehicle, **{column: None})))
        db.flush()


def test_upsert_key_rejects_a_second_leg_at_the_same_start_odometer(
    db: Session, add_vehicle: Callable[..., Vehicle], add_leg: Callable[..., ObservedTrip]
) -> None:
    vehicle = add_vehicle()
    add_leg(vehicle)
    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        db.add(ObservedTrip(**_leg_values(vehicle, end_time=BASE_TIME + timedelta(hours=2))))
        db.flush()
    assert _violated_constraint(exc) == "observed_trips_vehicle_odometer_start_uq"

    # Another vehicle may start a leg at the same odometer reading.
    other = add_vehicle()
    db.add(ObservedTrip(**_leg_values(other)))
    db.commit()


def test_upsert_on_start_odometer_extends_an_open_leg(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()

    def upsert(**overrides: Any) -> uuid.UUID:
        values = _leg_values(vehicle, **overrides)
        stmt = insert(ObservedTrip).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="observed_trips_vehicle_odometer_start_uq",
            set_={
                c: stmt.excluded[c]
                for c in (
                    "end_time",
                    "odometer_end_km",
                    "distance_km",
                    "soc_end_percent",
                    "soc_end_recorded_at",
                    "energy_kwh",
                    "closed",
                )
            }
            | {"updated_at": func.now()},
        ).returning(ObservedTrip.id)
        return db.execute(stmt).scalar_one()

    first = upsert(
        end_time=BASE_TIME + timedelta(minutes=20),
        odometer_end_km=Decimal("24670.00"),
        distance_km=Decimal("20.00"),
        soc_end_percent=Decimal("77"),
        soc_end_recorded_at=BASE_TIME + timedelta(minutes=20),
        energy_kwh=Decimal("2.130"),
        closed=False,
    )
    second = upsert()
    assert first == second
    db.expire_all()
    rows = db.scalars(select(ObservedTrip).where(ObservedTrip.vehicle_id == vehicle.id)).all()
    assert len(rows) == 1
    assert (rows[0].distance_km, rows[0].energy_kwh, rows[0].closed) == (
        Decimal("62.40"),
        Decimal("7.100"),
        True,
    )


# ---- prediction_results -----------------------------------------------------


def test_leg_and_plan_predictions_round_trip(
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_trip: Callable[..., uuid.UUID],
    add_leg: Callable[..., ObservedTrip],
    add_prediction: Callable[..., PredictionResult],
) -> None:
    vehicle = add_vehicle()
    leg = add_leg(vehicle)
    plan_id = add_trip(vehicle)
    leg_row = add_prediction(vehicle, observed_trip_id=leg.id)
    plan_row = add_prediction(
        vehicle,
        kind="plan",
        planned_trip_id=plan_id,
        distance_km=Decimal("63.10"),
        predicted_arrival_soc=Decimal("0.650"),
        observed_arrival_soc=Decimal("0.700"),
        observed_start_soc=Decimal("0.800"),
        arrival_soc_error_points=Decimal("5.00"),
        inputs={"plan_model_version": "deterministic-v1"},
    )
    db.expire_all()

    stored_leg = db.get(PredictionResult, leg_row.id)
    stored_plan = db.get(PredictionResult, plan_row.id)
    assert stored_leg is not None and stored_plan is not None
    assert (stored_leg.kind, stored_leg.observed_trip_id, stored_leg.planned_trip_id) == (
        "leg",
        leg.id,
        None,
    )
    assert stored_leg.inputs["baseline_source"] == "catalog"
    assert stored_leg.is_simulated is False
    assert (stored_plan.kind, stored_plan.planned_trip_id, stored_plan.observed_trip_id) == (
        "plan",
        plan_id,
        None,
    )
    assert stored_plan.arrival_soc_error_points == Decimal("5.00")


@pytest.mark.parametrize(
    ("constraint", "shape"),
    [
        ("prediction_results_kind_enum", "kind_unknown"),
        ("prediction_results_plan_iff_planned_trip", "plan_without_trip"),
        ("prediction_results_plan_iff_planned_trip", "leg_with_trip"),
        ("prediction_results_leg_iff_observed_trip", "leg_without_leg"),
        ("prediction_results_leg_iff_observed_trip", "plan_with_leg"),
        ("prediction_results_predicted_arrival_soc_range", "predicted_soc"),
        ("prediction_results_observed_arrival_soc_range", "observed_soc"),
        ("prediction_results_observed_start_soc_range", "start_soc"),
    ],
)
def test_prediction_check_constraints_reject_bad_rows(
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_trip: Callable[..., uuid.UUID],
    add_leg: Callable[..., ObservedTrip],
    constraint: str,
    shape: str,
) -> None:
    vehicle = add_vehicle()
    leg_id = add_leg(vehicle).id
    plan_id = add_trip(vehicle)
    leg_ok = {"kind": "leg", "observed_trip_id": leg_id}
    plan_ok = {"kind": "plan", "planned_trip_id": plan_id}
    overrides: dict[str, Any] = {
        "kind_unknown": {"kind": "chain", "observed_trip_id": leg_id},
        "plan_without_trip": {"kind": "plan"},
        "leg_with_trip": {**leg_ok, "planned_trip_id": plan_id},
        "leg_without_leg": {"kind": "leg"},
        "plan_with_leg": {**plan_ok, "observed_trip_id": leg_id},
        "predicted_soc": {**plan_ok, "predicted_arrival_soc": Decimal("1.001")},
        "observed_soc": {**plan_ok, "observed_arrival_soc": Decimal("-0.001")},
        "start_soc": {**plan_ok, "observed_start_soc": Decimal("1.5")},
    }[shape]
    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        db.add(PredictionResult(**_prediction_values(vehicle, **overrides)))
        db.flush()
    if shape == "kind_unknown":
        # 'chain' also fails the leg ↔ observed_trip check; any of them is fine.
        assert _violated_constraint(exc) in (
            "prediction_results_kind_enum",
            "prediction_results_leg_iff_observed_trip",
        )
    else:
        assert _violated_constraint(exc) == constraint


def test_one_prediction_per_leg_and_per_plan_per_model_version(
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_trip: Callable[..., uuid.UUID],
    add_leg: Callable[..., ObservedTrip],
    add_prediction: Callable[..., PredictionResult],
) -> None:
    vehicle = add_vehicle()
    leg = add_leg(vehicle)
    plan_id = add_trip(vehicle)
    add_prediction(vehicle, observed_trip_id=leg.id)
    add_prediction(vehicle, kind="plan", planned_trip_id=plan_id, inputs={})

    for constraint, overrides in (
        ("prediction_results_leg_model_uq", {"observed_trip_id": leg.id}),
        (
            "prediction_results_plan_model_uq",
            {"kind": "plan", "planned_trip_id": plan_id, "inputs": {}},
        ),
    ):
        with pytest.raises(IntegrityError) as exc, db.begin_nested():
            db.add(PredictionResult(**_prediction_values(vehicle, **overrides)))
            db.flush()
        assert _violated_constraint(exc) == constraint

    # A new model version scores the same leg and plan again.
    add_prediction(vehicle, observed_trip_id=leg.id, model_version="ml-v1")
    add_prediction(vehicle, kind="plan", planned_trip_id=plan_id, model_version="ml-v1", inputs={})
    # Many leg rows (planned_trip_id NULL) never collide on the plan key.
    other_leg = add_leg(
        vehicle, odometer_start_km=Decimal("25000.00"), odometer_end_km=Decimal("25062.40")
    )
    add_prediction(vehicle, observed_trip_id=other_leg.id)
    count = db.scalar(
        select(func.count())
        .select_from(PredictionResult)
        .where(PredictionResult.vehicle_id == vehicle.id)
    )
    assert count == 5


# ---- cascades -----------------------------------------------------------------


def _counts(db: Session, vehicle_id: uuid.UUID) -> tuple[int, int]:
    legs = db.scalar(
        select(func.count()).select_from(ObservedTrip).where(ObservedTrip.vehicle_id == vehicle_id)
    )
    predictions = db.scalar(
        select(func.count())
        .select_from(PredictionResult)
        .where(PredictionResult.vehicle_id == vehicle_id)
    )
    return int(legs or 0), int(predictions or 0)


@pytest.fixture()
def matched(
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_trip: Callable[..., uuid.UUID],
    add_leg: Callable[..., ObservedTrip],
    add_prediction: Callable[..., PredictionResult],
) -> dict[str, Any]:
    """A plan matched to a two-leg chain, with a leg row per leg and a plan row."""

    vehicle = add_vehicle()
    plan_id = add_trip(vehicle)
    leg_a = add_leg(vehicle, planned_trip_id=plan_id)
    leg_b = add_leg(
        vehicle,
        planned_trip_id=plan_id,
        start_time=BASE_TIME + timedelta(hours=2),
        start_time_latest=BASE_TIME + timedelta(hours=2),
        end_time=BASE_TIME + timedelta(hours=3),
        odometer_start_km=Decimal("24712.40"),
        odometer_end_km=Decimal("24800.00"),
        distance_km=Decimal("87.60"),
    )
    leg_rows = [add_prediction(vehicle, observed_trip_id=leg.id).id for leg in (leg_a, leg_b)]
    plan_row = add_prediction(vehicle, kind="plan", planned_trip_id=plan_id, inputs={}).id
    return {
        "vehicle": vehicle,
        "plan_id": plan_id,
        "legs": (leg_a.id, leg_b.id),
        "leg_rows": leg_rows,
        "plan_row": plan_row,
    }


def test_profile_delete_cascades_to_legs_and_predictions(
    db: Session, matched: dict[str, Any]
) -> None:
    vehicle_id, user_id = matched["vehicle"].id, matched["vehicle"].user_id
    assert _counts(db, vehicle_id) == (2, 3)
    db.execute(delete(Profile).where(Profile.id == user_id))
    db.commit()
    assert _counts(db, vehicle_id) == (0, 0)


def test_vehicle_delete_cascades_to_legs_and_predictions(
    db: Session, matched: dict[str, Any]
) -> None:
    vehicle_id = matched["vehicle"].id
    assert _counts(db, vehicle_id) == (2, 3)
    db.execute(delete(Vehicle).where(Vehicle.id == vehicle_id))
    db.commit()
    assert _counts(db, vehicle_id) == (0, 0)


def test_leg_delete_cascades_to_its_leg_prediction_only(
    db: Session, matched: dict[str, Any]
) -> None:
    leg_a, leg_b = matched["legs"]
    db.execute(delete(ObservedTrip).where(ObservedTrip.id == leg_a))
    db.commit()
    db.expire_all()
    remaining = set(db.scalars(select(PredictionResult.id)).all())
    assert matched["leg_rows"][0] not in remaining
    assert {matched["leg_rows"][1], matched["plan_row"]} <= remaining
    assert db.get(ObservedTrip, leg_b) is not None


def test_plan_delete_drops_its_prediction_and_unlinks_its_legs(
    db: Session, matched: dict[str, Any]
) -> None:
    db.execute(delete(PlannedTrip).where(PlannedTrip.id == matched["plan_id"]))
    db.commit()
    db.expire_all()

    assert db.get(PredictionResult, matched["plan_row"]) is None
    for leg_id, row_id in zip(matched["legs"], matched["leg_rows"], strict=True):
        leg = db.get(ObservedTrip, leg_id)
        assert leg is not None
        assert leg.planned_trip_id is None
        assert db.get(PredictionResult, row_id) is not None
