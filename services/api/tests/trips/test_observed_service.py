"""Inference runs over stored telemetry (Phase 5 F3, phase5_plan.md §3 F3).

Telemetry rows are inserted directly, then ``run_for_vehicle`` is called the
way the webhook hook and the recompute endpoint call it. Everything runs on
the shared Postgres test database, rolled back per test.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.trips import observed_service
from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.vehicles.models import Vehicle, VehicleSpecs, VehicleTelemetry
from app.modules.vehicles.telemetry_service import list_readings
from tests.trips.conftest import OTHER_USER_ID

T0 = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

# (minutes after T0, odometer km, soc %, is_charging); None = no reading.
Row = tuple[float, float | None, float | None, bool | None]

# Two closed one-pair legs:
#   A: 08:00 → 08:10, 1000 → 1010 km, SoC 80 → 75 (closed by 40 min at rest)
#   B: 08:50 → 09:00, 1010 → 1020 km, SoC 75 → 70 (closed by 40 min at rest)
TWO_LEGS: list[Row] = [
    (0, 1000.0, 80, False),
    (10, 1010.0, 75, None),
    (50, 1010.0, 75, None),
    (60, 1020.0, 70, None),
    (100, 1020.0, 70, None),
]


def add_readings(
    db: Session,
    vehicle: Vehicle,
    rows: Sequence[Row],
    *,
    is_simulated: bool | None = None,
    odometer_unit: str = "km",
) -> None:
    """Store readings as the webhook would (one ``vehicle_telemetry`` row per signal)."""

    simulated = vehicle.is_simulated if is_simulated is None else is_simulated
    for minutes, odo, soc, charging in rows:
        at = T0 + timedelta(minutes=minutes)
        for code, value, unit in (
            ("odometer", odo, odometer_unit),
            ("soc", soc, "percent"),
            ("charging_status", None if charging is None else int(charging), "bool"),
        ):
            if value is None:
                continue
            db.add(
                VehicleTelemetry(
                    vehicle_id=vehicle.id,
                    recorded_at=at,
                    received_at=at,
                    source="smartcar",
                    source_event_id=None,
                    signal_code=code,
                    value=Decimal(str(value)),
                    unit=unit,
                    raw_payload={},
                    is_simulated=simulated,
                )
            )
    db.commit()


def legs(db: Session, vehicle: Vehicle) -> list[ObservedTrip]:
    db.expire_all()
    return list(
        db.scalars(
            select(ObservedTrip)
            .where(ObservedTrip.vehicle_id == vehicle.id)
            .order_by(ObservedTrip.start_time)
        )
    )


def run(
    db: Session,
    vehicle: Vehicle,
    *,
    since: datetime | None = None,
    full: bool = False,
) -> observed_service.InferRunSummary:
    summary = observed_service.run_for_vehicle(
        db, vehicle_id=vehicle.id, user_id=vehicle.user_id, since=since, full=full, now=NOW
    )
    db.commit()
    return summary


def add_backtest(db: Session, leg: ObservedTrip) -> uuid.UUID:
    row = PredictionResult(
        user_id=leg.user_id,
        vehicle_id=leg.vehicle_id,
        kind="leg",
        observed_trip_id=leg.id,
        model_version="deterministic-v1",
        distance_km=leg.distance_km,
        observed_distance_km=leg.distance_km,
        predicted_energy_kwh=Decimal("2.000"),
        observed_energy_kwh=leg.energy_kwh,
        error_kwh=None,
        inputs={},
    )
    db.add(row)
    db.commit()
    return row.id


# ---------------------------------------------------------------------------


def test_creates_legs_with_numbers_capacity_and_review(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)

    summary = run(db, vehicle)

    assert (summary.legs_created, summary.legs_updated, summary.legs_deleted) == (2, 0, 0)
    a, b = legs(db, vehicle)
    assert (a.odometer_start_km, a.odometer_end_km, a.distance_km) == (
        Decimal("1000.00"),
        Decimal("1010.00"),
        Decimal("10.00"),
    )
    assert a.start_time == a.start_time_latest == T0
    assert a.end_time == T0 + timedelta(minutes=10)
    assert (a.soc_start_percent, a.soc_end_percent) == (Decimal("80.00"), Decimal("75.00"))
    # 5 points of the catalog's 71.0 kWh.
    assert a.energy_kwh == Decimal("3.550")
    assert a.energy_kwh_uncertainty == Decimal("0.710")
    assert a.energy_source == "soc_inferred"
    assert (a.usable_capacity_kwh, a.capacity_source) == (Decimal("71.00"), "catalog")
    assert a.quality_flag == "medium"
    assert a.review_status == "auto"
    assert a.closed is True
    assert a.inference_version == "telemetry-v1"
    assert a.user_id == vehicle.user_id
    assert a.is_simulated is False
    assert b.odometer_start_km == Decimal("1010.00")


def test_rerun_without_new_telemetry_changes_nothing(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    run(db, vehicle)
    before = {leg.id: (leg.updated_at, leg.energy_kwh) for leg in legs(db, vehicle)}

    for kwargs in ({}, {"full": True}, {"since": T0}):
        summary = run(db, vehicle, **kwargs)  # type: ignore[arg-type]
        assert (summary.legs_created, summary.legs_updated, summary.legs_deleted) == (0, 0, 0)

    after = {leg.id: (leg.updated_at, leg.energy_kwh) for leg in legs(db, vehicle)}
    assert after == before


def test_low_quality_leg_needs_review(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    # 2 SoC points: soc_delta_small → low.
    add_readings(db, vehicle, [(0, 500.0, 60, False), (10, 505.0, 58, None), (60, 505.0, 58, None)])

    run(db, vehicle)

    (leg,) = legs(db, vehicle)
    assert leg.quality_flag == "low"
    assert "soc_delta_small" in leg.quality_reasons
    assert leg.review_status == "needs_review"


def test_normal_run_only_rebuilds_from_the_anchor(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    run(db, vehicle)
    a, b = legs(db, vehicle)
    # Mark both legs; a windowed run must leave everything before the anchor
    # (B's end) alone, and B itself ends at the anchor.
    a.quality_reasons = ["marker"]
    b.quality_reasons = ["marker"]
    db.commit()
    # A new leg C after B.
    add_readings(db, vehicle, [(110, 1030.0, 65, None), (150, 1030.0, 65, None)])

    summary = run(db, vehicle, since=T0 + timedelta(minutes=110))

    assert summary.window_start == T0 + timedelta(minutes=60)
    assert summary.legs_created == 1
    a, b, c = legs(db, vehicle)
    assert a.quality_reasons == ["marker"]
    assert b.quality_reasons == ["marker"]
    assert c.odometer_start_km == Decimal("1020.00")

    # A full run rebuilds everything.
    summary = run(db, vehicle, full=True)
    assert summary.window_start is None
    assert summary.legs_updated == 2
    assert ["marker"] not in [leg.quality_reasons for leg in legs(db, vehicle)]


def test_open_leg_grows_with_later_readings(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, [(0, 1000.0, 80, False), (10, 1010.0, 76, None)])
    run(db, vehicle, since=T0)
    (leg,) = legs(db, vehicle)
    assert leg.closed is False
    first_id = leg.id

    add_readings(db, vehicle, [(20, 1022.0, 71, None)])
    summary = run(db, vehicle, since=T0 + timedelta(minutes=20))

    assert (summary.legs_created, summary.legs_updated) == (0, 1)
    (leg,) = legs(db, vehicle)
    assert leg.id == first_id  # upserted on its start odometer
    assert leg.odometer_end_km == Decimal("1022.00")
    assert leg.closed is False


def test_late_charging_inside_a_closed_leg_nulls_its_energy(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    run(db, vehicle)
    a, b = legs(db, vehicle)
    a_backtest = add_backtest(db, a)
    b_backtest = add_backtest(db, b)

    # A delayed charging session strictly inside A's only odometer pair.
    add_readings(db, vehicle, [(4, None, None, True), (6, None, None, False)])
    summary = run(db, vehicle, since=T0 + timedelta(minutes=4))

    assert summary.window_start == T0  # moved back to A's start, no full run
    assert summary.backtests_removed == 1
    a, b = legs(db, vehicle)
    assert a.odometer_start_km == Decimal("1000.00")
    assert a.energy_kwh is None
    assert a.energy_source is None
    assert "soc_increased" in a.quality_reasons
    assert b.energy_kwh == Decimal("3.550")
    remaining = set(db.scalars(select(PredictionResult.id)))
    assert a_backtest not in remaining
    assert b_backtest in remaining


def test_late_reading_older_than_every_leg_moves_window_to_since(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    run(db, vehicle)
    a, _ = legs(db, vehicle)
    observed_service.confirm(db, user_id=vehicle.user_id, trip_id=a.id, now=NOW)
    db.commit()
    observed_service.reject(db, user_id=vehicle.user_id, trip_id=legs(db, vehicle)[1].id, now=NOW)
    db.commit()

    since = T0 - timedelta(hours=3)
    summary = run(db, vehicle, since=since)

    # Both legs reaching `since` are frozen, so the window starts at `since`.
    assert summary.window_start == since
    assert summary.legs_skipped_frozen == 2
    assert (summary.legs_created, summary.legs_updated, summary.legs_deleted) == (0, 0, 0)


def test_frozen_legs_survive_a_full_run(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    run(db, vehicle)
    a, b = legs(db, vehicle)
    observed_service.confirm(db, user_id=vehicle.user_id, trip_id=a.id, now=NOW)
    observed_service.reject(db, user_id=vehicle.user_id, trip_id=b.id, now=NOW)
    db.commit()
    a_before = (a.energy_kwh, a.quality_reasons, a.updated_at)

    # New telemetry would change A (charging inside it) and delete B (its
    # readings are gone).
    add_readings(db, vehicle, [(4, None, None, True), (6, None, None, False)])
    db.execute(
        delete(VehicleTelemetry).where(
            VehicleTelemetry.vehicle_id == vehicle.id,
            VehicleTelemetry.recorded_at >= T0 + timedelta(minutes=50),
        )
    )
    db.commit()

    summary = run(db, vehicle, full=True)

    assert summary.legs_deleted == 0
    a, b = legs(db, vehicle)
    assert a.review_status == "confirmed"
    assert (a.energy_kwh, a.quality_reasons, a.updated_at) == a_before
    assert b.review_status == "rejected"
    assert summary.legs_skipped_frozen >= 1


def test_leg_that_disappears_is_deleted(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    run(db, vehicle)
    _, b = legs(db, vehicle)
    add_backtest(db, b)
    db.execute(
        delete(VehicleTelemetry).where(
            VehicleTelemetry.vehicle_id == vehicle.id,
            VehicleTelemetry.recorded_at >= T0 + timedelta(minutes=60),
        )
    )
    db.commit()

    summary = run(db, vehicle, full=True)

    assert summary.legs_deleted == 1
    assert [leg.odometer_start_km for leg in legs(db, vehicle)] == [Decimal("1000.00")]
    assert db.scalar(select(PredictionResult.id)) is None  # cascaded with its leg


def test_simulated_vehicle_gets_simulated_legs_and_drops_real_rows(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    vehicle.is_simulated = True
    db.commit()
    add_readings(db, vehicle, TWO_LEGS)
    # A real-flagged reading in a simulated vehicle's feed is dropped (D11).
    add_readings(db, vehicle, [(5, 1500.0, None, None)], is_simulated=False)

    summary = run(db, vehicle)

    assert summary.dropped_simulated_readings == 1
    assert [leg.is_simulated for leg in legs(db, vehicle)] == [True, True]


def test_unknown_capacity_and_vehicle_override(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    vehicle.model = "Unknown model"
    db.commit()
    add_readings(db, vehicle, TWO_LEGS)
    run(db, vehicle)
    a = legs(db, vehicle)[0]
    assert (a.energy_kwh, a.usable_capacity_kwh, a.capacity_source) == (None, None, None)
    assert "capacity_unknown" in a.quality_reasons

    db.add(VehicleSpecs(vehicle_id=vehicle.id, usable_capacity_kwh=Decimal("60.00")))
    db.commit()
    run(db, vehicle, full=True)
    a = legs(db, vehicle)[0]
    assert (a.usable_capacity_kwh, a.capacity_source) == (Decimal("60.00"), "vehicle")
    assert a.energy_kwh == Decimal("3.000")


def test_odometer_in_miles_is_converted(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    add_readings(
        db,
        vehicle,
        [(0, 100.0, 80, False), (10, 110.0, 75, None), (60, 110.0, 75, None)],
        odometer_unit="miles",
    )
    run(db, vehicle)
    (leg,) = legs(db, vehicle)
    assert leg.odometer_start_km == Decimal("160.93")
    assert leg.odometer_end_km == Decimal("177.03")
    assert leg.distance_km == Decimal("16.10")


def test_list_readings_window_includes_prior_state_and_drops_errors(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    # An errored reading and one with a unit we don't know: both dropped.
    for code, unit, value in (("soc", "percent", None), ("odometer", "furlongs", Decimal("9"))):
        db.add(
            VehicleTelemetry(
                vehicle_id=vehicle.id,
                recorded_at=T0 + timedelta(minutes=55),
                received_at=T0,
                source="smartcar",
                signal_code=code,
                value=value,
                unit=unit,
                raw_payload={},
            )
        )
    db.commit()

    readings = list_readings(db, vehicle_id=vehicle.id, since=T0 + timedelta(minutes=50))

    by_code = {(r.code, r.recorded_at - T0) for r in readings}
    # The latest odometer, SoC and charging state before the window …
    assert ("odometer", timedelta(minutes=10)) in by_code
    assert ("soc", timedelta(minutes=10)) in by_code
    assert ("charging_status", timedelta(0)) in by_code
    # … and nothing errored, unconvertible or older.
    assert all(r.recorded_at != T0 + timedelta(minutes=55) for r in readings)
    assert ("odometer", timedelta(0)) not in by_code
    assert readings == sorted(readings, key=lambda r: (r.recorded_at, r.code, r.value))


def test_run_for_someone_elses_vehicle_is_not_found(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle(user_id=OTHER_USER_ID)
    with pytest.raises(ApiError) as exc:
        observed_service.run_for_vehicle(db, vehicle_id=vehicle.id, user_id=uuid.uuid4(), now=NOW)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# PR #53 review fixes (Codex)
# ---------------------------------------------------------------------------

# Drive 1 08:00 → 08:10, at rest until 08:50, drive 2 08:50 → 09:00, then at
# rest. SoC only at 08:00 (80) and 09:00 (70): neither leg has its own pair of
# SoC readings, so both must have energy None (F2's SoC bounds).
SOC_STEAL: list[Row] = [
    (0, 1000.0, 80, False),
    (10, 1010.0, None, None),
    (50, 1010.0, None, None),
    (60, 1020.0, 70, None),
    (100, 1020.0, None, None),
]


def _numbers(db: Session, vehicle: Vehicle) -> list[tuple[object, ...]]:
    return [
        (
            leg.odometer_start_km,
            leg.odometer_end_km,
            leg.soc_start_percent,
            leg.soc_end_percent,
            leg.energy_kwh,
            tuple(leg.quality_reasons),
        )
        for leg in legs(db, vehicle)
    ]


def test_windowed_run_doesnt_take_soc_from_before_the_previous_leg(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, SOC_STEAL)
    run(db, vehicle, full=True)
    full = _numbers(db, vehicle)
    assert [n[4] for n in full] == [None, None]

    # A late odometer reading inside drive 2 moves the window back to drive 2's
    # start (08:50); drive 1 is outside the window.
    add_readings(db, vehicle, [(55, 1015.0, None, None)])
    summary = run(db, vehicle, since=T0 + timedelta(minutes=55))

    assert summary.window_start == T0 + timedelta(minutes=50)
    assert _numbers(db, vehicle) == full  # windowed == full: energy stays None
    assert run(db, vehicle, full=True).legs_updated == 0


def test_late_drive_before_every_leg_is_created(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    # An 08:00 drive, stored and closed.
    add_readings(
        db, vehicle, [(0, 1000.0, 80, False), (10, 1010.0, 75, None), (50, 1010.0, 75, None)]
    )
    run(db, vehicle, since=T0)
    (later,) = legs(db, vehicle)
    observed_service.confirm(db, user_id=vehicle.user_id, trip_id=later.id, now=NOW)
    db.commit()

    # A delayed 07:00 → 07:10 drive (990 → 1000 km), then at rest until 08:00.
    add_readings(db, vehicle, [(-60, 990.0, 88, False), (-50, 1000.0, 84, None)])
    summary = run(db, vehicle, since=T0 - timedelta(minutes=60))

    assert summary.window_start == T0 - timedelta(minutes=60)
    assert summary.legs_created == 1
    earlier, later_again = legs(db, vehicle)
    assert (earlier.odometer_start_km, earlier.odometer_end_km) == (
        Decimal("990.00"),
        Decimal("1000.00"),
    )
    assert earlier.start_time == T0 - timedelta(minutes=60)
    # The confirmed 08:00 leg is still frozen.
    assert later_again.id == later.id
    assert later_again.review_status == "confirmed"
    assert later_again.energy_kwh == later.energy_kwh
