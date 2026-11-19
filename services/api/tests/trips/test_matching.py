"""Plan ↔ observation matching and backtests (Phase 5 F4, phase5_plan.md §3 F4).

The pure core (``match_plans``, ``score_plan``, ``chain_fits``) is tested on
dataclasses. The database path runs §5 example 3 end to end: the F2 telemetry
fixture is stored as webhook rows, ``run_for_vehicle`` infers the legs and
calls matching through the one F3 call site, and ``GET /trips/{id}`` returns
the §3.18e observation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.trips import observed_service
from app.modules.trips.matching import (
    LegCandidate,
    PlanCandidate,
    backtest_leg,
    chain_fits,
    match_plans,
    planned_distance_km,
    run_matching,
    score_plan,
)
from app.modules.trips.models import PlannedTrip
from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.trips.service import NewPlannedStop, create_planned_trip
from app.modules.vehicles.models import Vehicle, VehicleSpecs, VehicleTelemetry
from tests.trips.conftest import DESTINATION, ORIGIN
from tests.trips.test_observed_service import TWO_LEGS, add_readings

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "telemetry"
OBSERVED = "/api/v1/observed-trips"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# §5 example 3, as data
# ---------------------------------------------------------------------------

EX3_CREATED = _ts("2026-11-07T21:10:00Z")
EX3_DEPARTURE = _ts("2026-11-08T08:00:00Z")
EX3_NOW = _ts("2026-11-08T13:45:00Z")

# phase4_plan §5 trip 2: 400 km, stop B at 220 km along / 1.0 km off route.
EX3_PAYLOAD: dict[str, Any] = {
    "status": "feasible",
    "model_version": "deterministic-v1",
    "starting_soc": 0.8,
    "route": {"route_index": 0, "distance_km": 400.0, "drive_minutes": 240},
    "stops": [
        {
            "sequence": 1,
            "distance_along_route_km": 220.0,
            "distance_from_route_km": 1.0,
            "arrival_soc": 0.173,
            "departure_soc": 0.66,
            "charging_minutes": 18,
        }
    ],
    "arrival_soc": 0.15296,
    "totals": {"total_minutes": 300, "charging_minutes": 18, "energy_kwh_estimated": 80.52},
    "assumptions": {"detour_road_factor": 1.3},
    "alternatives": [],
    "reason": None,
}

LEG1 = LegCandidate(
    leg_id=uuid.UUID(int=1),
    start_time=_ts("2026-11-08T08:02:00Z"),
    start_time_latest=_ts("2026-11-08T08:02:00Z"),
    end_time=_ts("2026-11-08T10:32:00Z"),
    distance_km=222.40,
    energy_kwh=44.02,
    soc_start_percent=80,
    soc_end_percent=18,
)
LEG2 = LegCandidate(
    leg_id=uuid.UUID(int=2),
    start_time=_ts("2026-11-08T10:53:00Z"),
    start_time_latest=_ts("2026-11-08T10:53:00Z"),
    end_time=_ts("2026-11-08T13:00:00Z"),
    distance_km=183.60,
    energy_kwh=36.21,
    soc_start_percent=66,
    soc_end_percent=15,
)


def plan(
    *,
    trip_id: int = 100,
    created_at: datetime = EX3_CREATED,
    departure_time: datetime | None = EX3_DEPARTURE,
    saved: bool = True,
    planned_km: float = 402.6,
) -> PlanCandidate:
    return PlanCandidate(
        trip_id=uuid.UUID(int=trip_id),
        created_at=created_at,
        departure_time=departure_time,
        saved=saved,
        planned_distance_km=planned_km,
        predicted_energy_kwh=80.52,
        predicted_arrival_soc=0.15296,
        starting_soc=0.8,
    )


def shifted(leg: LegCandidate, *, minutes: float, leg_id: int | None = None) -> LegCandidate:
    delta = timedelta(minutes=minutes)
    return LegCandidate(
        leg_id=uuid.UUID(int=leg_id) if leg_id is not None else leg.leg_id,
        start_time=leg.start_time + delta,
        start_time_latest=leg.start_time_latest + delta,
        end_time=leg.end_time + delta,
        distance_km=leg.distance_km,
        energy_kwh=leg.energy_kwh,
        soc_start_percent=leg.soc_start_percent,
        soc_end_percent=leg.soc_end_percent,
    )


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------


def test_planned_distance_adds_round_trip_detours() -> None:
    assert planned_distance_km(EX3_PAYLOAD) == pytest.approx(402.6)
    assert planned_distance_km({"route": None}) is None


def test_example3_matches_a_chain_of_two_legs() -> None:
    (match,) = match_plans([plan()], [LEG2, LEG1])
    assert match.leg_ids == (LEG1.leg_id, LEG2.leg_id)
    assert match.observed_distance_km == pytest.approx(406.00)
    assert match.distance_error_km == pytest.approx(3.40)


def test_example3_score() -> None:
    score = score_plan(plan(), [LEG1, LEG2])
    assert score.observed_energy_kwh == pytest.approx(80.23, abs=0.005)
    assert score.error_kwh == pytest.approx(-0.29, abs=0.005)
    assert score.observed_start_soc == pytest.approx(0.80)
    assert score.observed_arrival_soc == pytest.approx(0.15)
    assert score.arrival_soc_error_points == pytest.approx(-0.30, abs=0.005)


def test_score_energy_is_null_when_any_leg_lacks_energy() -> None:
    no_energy = LegCandidate(**{**LEG2.__dict__, "energy_kwh": None})
    score = score_plan(plan(), [LEG1, no_energy])
    assert score.observed_energy_kwh is None
    assert score.error_kwh is None
    assert score.observed_arrival_soc == pytest.approx(0.15)


def test_leg_alone_is_outside_the_distance_tolerance() -> None:
    assert match_plans([plan()], [LEG1]) == ()


def test_no_match_outside_the_departure_window() -> None:
    late = [shifted(LEG1, minutes=125), shifted(LEG2, minutes=125)]
    assert match_plans([plan()], late) == ()
    # Inside the bound: still a match.
    on_time = [shifted(LEG1, minutes=110), shifted(LEG2, minutes=110)]
    assert len(match_plans([plan()], on_time)) == 1


def test_bounded_start_matches_when_its_bound_reaches_the_window() -> None:
    bounded = LegCandidate(
        **{**LEG1.__dict__, "start_time": _ts("2026-11-07T22:00:00Z")}
    )  # start somewhere in 22:00 … 08:02
    assert len(match_plans([plan()], [bounded, LEG2])) == 1


def test_without_departure_time_the_window_is_around_created_at() -> None:
    p = plan(departure_time=None, created_at=_ts("2026-11-08T07:00:00Z"))
    assert len(match_plans([p], [LEG1, LEG2])) == 1
    # Created more than 15 min after the drive started: no match.
    after = plan(departure_time=None, created_at=_ts("2026-11-08T08:20:00Z"))
    assert match_plans([after], [LEG1, LEG2]) == ()
    # More than 24 h before: no match.
    early = plan(departure_time=None, created_at=_ts("2026-11-07T08:00:00Z"))
    assert match_plans([early], [LEG1, LEG2]) == ()


def test_no_match_outside_the_distance_tolerance() -> None:
    # 406.0 km driven; tolerance is max(5, 10 %).
    assert match_plans([plan(planned_km=460.0)], [LEG1, LEG2]) == ()
    assert len(match_plans([plan(planned_km=450.0)], [LEG1, LEG2])) == 1
    # Short trips use the 5 km floor.
    short = LegCandidate(**{**LEG1.__dict__, "distance_km": 30.0})
    assert len(match_plans([plan(planned_km=25.5)], [short])) == 1
    assert match_plans([plan(planned_km=24.0)], [short]) == ()


def test_chain_stops_at_a_gap_over_120_minutes() -> None:
    far = shifted(LEG2, minutes=100)  # gap 10:32 → 12:33 = 121 min
    assert match_plans([plan()], [LEG1, far]) == ()
    near = shifted(LEG2, minutes=99)  # exactly 120 min
    assert len(match_plans([plan()], [LEG1, near])) == 1


def test_two_plans_for_one_drive_prefer_the_departure_window() -> None:
    with_departure = plan(trip_id=1, saved=False, created_at=_ts("2026-11-08T07:00:00Z"))
    without = plan(trip_id=2, departure_time=None, created_at=_ts("2026-11-08T07:59:00Z"))
    (match,) = match_plans([without, with_departure], [LEG1, LEG2])
    assert match.trip_id == with_departure.trip_id


def test_two_plans_for_one_drive_then_saved_then_latest() -> None:
    created = _ts("2026-11-08T07:50:00Z")
    unsaved = plan(trip_id=1, departure_time=None, saved=False, created_at=created)
    saved = plan(
        trip_id=2, departure_time=None, saved=True, created_at=created - timedelta(minutes=30)
    )
    (match,) = match_plans([unsaved, saved], [LEG1, LEG2])
    assert match.trip_id == saved.trip_id

    older = plan(trip_id=3, departure_time=None, saved=False, created_at=created)
    newer = plan(
        trip_id=4, departure_time=None, saved=False, created_at=created + timedelta(minutes=5)
    )
    (match,) = match_plans([older, newer], [LEG1, LEG2])
    assert match.trip_id == newer.trip_id


def test_one_leg_is_never_in_two_chains() -> None:
    a = plan(trip_id=1)
    b = plan(trip_id=2, saved=False)
    matches = match_plans([a, b], [LEG1, LEG2])
    assert len(matches) == 1
    used = [leg for m in matches for leg in m.leg_ids]
    assert len(used) == len(set(used))


def test_two_drives_two_plans_each_get_their_own_chain() -> None:
    tomorrow = [
        shifted(LEG1, minutes=24 * 60, leg_id=11),
        shifted(LEG2, minutes=24 * 60, leg_id=12),
    ]
    a = plan(trip_id=1)
    b = plan(trip_id=2, departure_time=EX3_DEPARTURE + timedelta(days=1))
    matches = {m.trip_id: m.leg_ids for m in match_plans([a, b], [LEG1, LEG2, *tomorrow])}
    assert matches == {
        a.trip_id: (LEG1.leg_id, LEG2.leg_id),
        b.trip_id: (uuid.UUID(int=11), uuid.UUID(int=12)),
    }


def test_chain_fits_rechecks_an_existing_match() -> None:
    assert chain_fits(plan(), [LEG1, LEG2])
    assert not chain_fits(plan(), [LEG2])
    assert not chain_fits(plan(), [])


def test_backtest_leg_is_distance_times_baseline() -> None:
    assert backtest_leg(0.20, 222.40) == pytest.approx(44.48)
    with pytest.raises(ValueError):
        backtest_leg(0.0, 10.0)
    with pytest.raises(ValueError):
        backtest_leg(0.2, -1.0)


# ---------------------------------------------------------------------------
# Database: example 3 end to end
# ---------------------------------------------------------------------------

_CODE = {
    "odometer_km": ("odometer", "km"),
    "soc_percent": ("soc", "percent"),
    "is_charging": ("charging_status", "bool"),
}


def store_fixture(db: Session, vehicle: Vehicle, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    for r in data["readings"]:
        code, unit = _CODE[r["signal"]]
        at = _ts(r["recorded_at"])
        db.add(
            VehicleTelemetry(
                vehicle_id=vehicle.id,
                recorded_at=at,
                received_at=at,
                source="smartcar",
                source_event_id=None,
                signal_code=code,
                value=Decimal(str(r["value"])),
                unit=unit,
                raw_payload={},
                is_simulated=r["is_simulated"],
            )
        )
    db.commit()
    return data


def add_plan(
    db: Session,
    vehicle: Vehicle,
    *,
    payload: dict[str, Any] = EX3_PAYLOAD,
    created_at: datetime = EX3_CREATED,
    departure_time: datetime | None = EX3_DEPARTURE,
    saved: bool = True,
    plan_status: str = "feasible",
) -> uuid.UUID:
    tid = uuid.uuid4()
    body = {**payload, "trip_id": str(tid)}
    create_planned_trip(
        db,
        trip_id=tid,
        created_at=created_at,
        user_id=vehicle.user_id,
        vehicle_id=vehicle.id,
        origin=ORIGIN,
        destination=DESTINATION,
        departure_time=departure_time,
        plan_status=plan_status,  # type: ignore[arg-type]
        plan_payload=body,
        model_version="deterministic-v1",
        stops=[
            NewPlannedStop(
                sequence=1,
                station_id=None,
                arrival_soc=0.173,
                departure_soc=0.66,
                charging_minutes=18,
            )
        ]
        if payload.get("stops")
        else [],
    )
    if saved:
        trip = db.get(PlannedTrip, tid)
        assert trip is not None
        trip.saved_at = created_at + timedelta(minutes=1)
    db.commit()
    return tid


def infer(
    db: Session, vehicle: Vehicle, *, now: datetime = EX3_NOW
) -> observed_service.InferRunSummary:
    summary = observed_service.run_for_vehicle(
        db, vehicle_id=vehicle.id, user_id=vehicle.user_id, now=now
    )
    db.commit()
    db.expire_all()
    return summary


def vehicle_legs(db: Session, vehicle: Vehicle) -> list[ObservedTrip]:
    return list(
        db.scalars(
            select(ObservedTrip)
            .where(ObservedTrip.vehicle_id == vehicle.id)
            .order_by(ObservedTrip.start_time)
        )
    )


def predictions(db: Session, vehicle: Vehicle, kind: str) -> list[PredictionResult]:
    return list(
        db.scalars(
            select(PredictionResult)
            .where(PredictionResult.vehicle_id == vehicle.id, PredictionResult.kind == kind)
            .order_by(PredictionResult.distance_km.desc())
        )
    )


@pytest.fixture()
def ex3(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> tuple[Vehicle, uuid.UUID, observed_service.InferRunSummary]:
    vehicle = add_vehicle()
    store_fixture(db, vehicle, "example3_road_trip_dc_stop")
    plan_id = add_plan(db, vehicle)
    return vehicle, plan_id, infer(db, vehicle)


def test_example3_end_to_end_match_and_scores(
    db: Session, ex3: tuple[Vehicle, uuid.UUID, observed_service.InferRunSummary]
) -> None:
    vehicle, plan_id, summary = ex3
    assert summary.plans_matched == 1

    leg1, leg2 = vehicle_legs(db, vehicle)
    assert (leg1.distance_km, leg1.energy_kwh) == (Decimal("222.40"), Decimal("44.020"))
    assert (leg2.distance_km, leg2.energy_kwh) == (Decimal("183.60"), Decimal("36.210"))
    assert leg1.planned_trip_id == plan_id
    assert leg2.planned_trip_id == plan_id
    trip = db.get(PlannedTrip, plan_id)
    assert trip is not None and trip.status == "completed"

    (row,) = predictions(db, vehicle, "plan")
    assert row.planned_trip_id == plan_id
    assert row.model_version == "deterministic-v1"
    assert row.distance_km == Decimal("402.60")
    assert row.observed_distance_km == Decimal("406.00")
    assert row.predicted_energy_kwh == Decimal("80.520")
    assert row.observed_energy_kwh == Decimal("80.230")
    assert row.error_kwh == Decimal("-0.290")
    assert row.observed_start_soc == Decimal("0.800")
    assert row.predicted_arrival_soc == Decimal("0.153")
    assert row.observed_arrival_soc == Decimal("0.150")
    assert row.arrival_soc_error_points == Decimal("-0.30")
    assert row.inputs == {"plan_model_version": "deterministic-v1"}
    assert row.created_at == EX3_NOW
    assert row.is_simulated is False

    backtests = predictions(db, vehicle, "leg")
    assert [
        (b.observed_trip_id, b.predicted_energy_kwh, b.observed_energy_kwh, b.error_kwh)
        for b in backtests
    ] == [
        (leg1.id, Decimal("44.480"), Decimal("44.020"), Decimal("-0.460")),
        (leg2.id, Decimal("36.720"), Decimal("36.210"), Decimal("-0.510")),
    ]
    assert backtests[0].inputs == {
        "baseline_kwh_per_km": 0.2,
        "baseline_source": "catalog",
        "usable_capacity_kwh": 71.0,
    }


def test_example3_rerun_changes_nothing(
    db: Session, ex3: tuple[Vehicle, uuid.UUID, observed_service.InferRunSummary]
) -> None:
    vehicle, _, _ = ex3
    before = [(r.id, r.updated_at) for r in predictions(db, vehicle, "plan")] + [
        (r.id, r.updated_at) for r in predictions(db, vehicle, "leg")
    ]
    again = infer(db, vehicle, now=EX3_NOW + timedelta(hours=1))
    assert again.plans_matched == 0
    after = [(r.id, r.updated_at) for r in predictions(db, vehicle, "plan")] + [
        (r.id, r.updated_at) for r in predictions(db, vehicle, "leg")
    ]
    assert after == before


def test_trip_detail_returns_the_observation(
    api: TestClient,
    auth: Callable[..., dict[str, str]],
    db: Session,
    ex3: tuple[Vehicle, uuid.UUID, observed_service.InferRunSummary],
) -> None:
    vehicle, plan_id, _ = ex3
    leg1, leg2 = vehicle_legs(db, vehicle)
    body = api.get(f"/api/v1/trips/{plan_id}", headers=auth()).json()
    assert body["status"] == "completed"
    assert body["observation"] == {
        "matched_at": "2026-11-08T13:45:00Z",
        "legs": [
            {
                "id": str(leg1.id),
                "plan_leg_index": 1,
                "start_time": "2026-11-08T08:02:00Z",
                "end_time": "2026-11-08T10:32:00Z",
                "distance_km": 222.4,
                "soc_start_percent": 80.0,
                "soc_end_percent": 18.0,
                "energy_kwh": 44.02,
                "quality_flag": "high",
                "review_status": "auto",
            },
            {
                "id": str(leg2.id),
                "plan_leg_index": 2,
                "start_time": "2026-11-08T10:53:00Z",
                "end_time": "2026-11-08T13:00:00Z",
                "distance_km": 183.6,
                "soc_start_percent": 66.0,
                "soc_end_percent": 15.0,
                "energy_kwh": 36.21,
                "quality_flag": "high",
                "review_status": "auto",
            },
        ],
        "observed_distance_km": 406.0,
        "planned_distance_km": 402.6,
        "predicted_energy_kwh": 80.52,
        "observed_energy_kwh": 80.23,
        "error_kwh": -0.29,
        "observed_start_soc": 0.8,
        "predicted_arrival_soc": 0.153,
        "observed_arrival_soc": 0.15,
        "arrival_soc_error_points": -0.3,
        "model_version": "deterministic-v1",
    }
    # The legs' API shows the derived position too.
    items = api.get(OBSERVED, headers=auth()).json()["items"]
    assert {i["id"]: i["plan_leg_index"] for i in items} == {str(leg1.id): 1, str(leg2.id): 2}


def test_unmatched_trip_has_null_observation(
    api: TestClient,
    auth: Callable[..., dict[str, str]],
    db: Session,
    add_vehicle: Callable[..., Vehicle],
) -> None:
    vehicle = add_vehicle()
    plan_id = add_plan(db, vehicle)
    body = api.get(f"/api/v1/trips/{plan_id}", headers=auth()).json()
    assert body["status"] == "planned"
    assert body["observation"] is None


def test_rejecting_a_matched_leg_unmatches_and_rescores(
    api: TestClient,
    auth: Callable[..., dict[str, str]],
    db: Session,
    ex3: tuple[Vehicle, uuid.UUID, observed_service.InferRunSummary],
) -> None:
    vehicle, plan_id, _ = ex3
    _leg1, leg2 = vehicle_legs(db, vehicle)
    res = api.post(f"{OBSERVED}/{leg2.id}/reject", headers=auth())
    assert res.status_code == 200
    assert res.json()["planned_trip_id"] is None

    db.expire_all()
    # Leg 1 alone (222.4 km) doesn't fit 402.6 km, so the plan is un-matched.
    assert [leg.planned_trip_id for leg in vehicle_legs(db, vehicle)] == [None, None]
    trip = db.get(PlannedTrip, plan_id)
    assert trip is not None and trip.status == "planned"
    assert predictions(db, vehicle, "plan") == []
    body = api.get(f"/api/v1/trips/{plan_id}", headers=auth()).json()
    assert body["status"] == "planned"
    assert body["observation"] is None
    # Backtests stay: they judge the baseline, not the plan.
    assert len(predictions(db, vehicle, "leg")) == 2

    # Confirming it again makes it matchable again on the next run.
    api.post(f"{OBSERVED}/{leg2.id}/confirm", headers=auth())
    summary = infer(db, vehicle, now=EX3_NOW + timedelta(hours=1))
    assert summary.plans_matched == 1
    assert {leg.planned_trip_id for leg in vehicle_legs(db, vehicle)} == {plan_id}


def test_rejecting_a_small_leg_keeps_a_chain_that_still_fits(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    store_fixture(db, vehicle, "example3_road_trip_dc_stop")
    plan_id = add_plan(db, vehicle)
    infer(db, vehicle)
    _leg1, leg2 = vehicle_legs(db, vehicle)
    # Rewrite the stored plan to 222 km, so leg 1 alone still fits once leg 2 is rejected.
    trip = db.get(PlannedTrip, plan_id)
    assert trip is not None
    trip.plan_payload = {
        **trip.plan_payload,
        "route": {"route_index": 0, "distance_km": 222.0, "drive_minutes": 150},
        "stops": [],
        "totals": {**trip.plan_payload["totals"], "energy_kwh_estimated": 44.4},
    }
    db.commit()
    observed_service.reject(db, user_id=vehicle.user_id, trip_id=leg2.id, now=EX3_NOW)
    db.commit()
    db.expire_all()
    (row,) = predictions(db, vehicle, "plan")
    assert row.observed_distance_km == Decimal("222.40")
    assert row.observed_energy_kwh == Decimal("44.020")
    assert row.observed_arrival_soc == Decimal("0.180")
    assert vehicle_legs(db, vehicle)[0].planned_trip_id == plan_id
    trip = db.get(PlannedTrip, plan_id)
    assert trip is not None and trip.status == "completed"


def test_infeasible_plan_never_matches(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    store_fixture(db, vehicle, "example3_road_trip_dc_stop")
    plan_id = add_plan(db, vehicle, plan_status="infeasible")
    assert infer(db, vehicle).plans_matched == 0
    trip = db.get(PlannedTrip, plan_id)
    assert trip is not None and trip.status == "planned"
    assert predictions(db, vehicle, "plan") == []


def test_plans_older_than_seven_days_are_not_candidates(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    store_fixture(db, vehicle, "example3_road_trip_dc_stop")
    add_plan(db, vehicle)
    assert infer(db, vehicle, now=EX3_CREATED + timedelta(days=8)).plans_matched == 0


def test_plan_on_another_vehicle_never_matches(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    other = add_vehicle()
    store_fixture(db, vehicle, "example3_road_trip_dc_stop")
    add_plan(db, other)
    assert infer(db, vehicle).plans_matched == 0


# ---------------------------------------------------------------------------
# Backtests (D9)
# ---------------------------------------------------------------------------


def test_backtests_for_every_finished_leg_with_energy(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    infer(db, vehicle, now=datetime(2026, 11, 10, 12, 0, tzinfo=UTC))
    a, b = vehicle_legs(db, vehicle)
    rows = {r.observed_trip_id: r for r in predictions(db, vehicle, "leg")}
    assert set(rows) == {a.id, b.id}
    # 10 km * 0.20 = 2.000 kWh predicted; 5 points * 0.71 = 3.550 observed.
    assert rows[a.id].predicted_energy_kwh == Decimal("2.000")
    assert rows[a.id].observed_energy_kwh == Decimal("3.550")
    assert rows[a.id].error_kwh == Decimal("1.550")


def test_no_backtest_for_an_in_progress_leg(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    rows: Sequence[tuple[float, float | None, float | None, bool | None]] = [
        (0, 1000.0, 80, False),
        (10, 1010.0, 75, None),
    ]
    add_readings(db, vehicle, rows)
    t0 = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    infer(db, vehicle, now=t0 + timedelta(minutes=15))
    (leg,) = vehicle_legs(db, vehicle)
    assert leg.closed is False and leg.energy_kwh is not None
    assert predictions(db, vehicle, "leg") == []
    # Once it's no longer in progress, the next run writes its backtest.
    infer(db, vehicle, now=t0 + timedelta(minutes=40))
    assert len(predictions(db, vehicle, "leg")) == 1


def test_backtest_follows_the_vehicle_baseline(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    infer(db, vehicle, now=now)
    db.add(VehicleSpecs(vehicle_id=vehicle.id, baseline_kwh_per_km=Decimal("0.250")))
    db.commit()
    infer(db, vehicle, now=now)
    rows = predictions(db, vehicle, "leg")
    assert {r.predicted_energy_kwh for r in rows} == {Decimal("2.500")}
    assert {r.inputs["baseline_source"] for r in rows} == {"vehicle"}
    assert {r.error_kwh for r in rows} == {Decimal("1.050")}


def test_backtest_rewritten_after_a_leg_energy_change(
    db: Session, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    infer(db, vehicle, now=now)
    a, _ = vehicle_legs(db, vehicle)
    a.energy_kwh = Decimal("4.000")
    db.commit()
    summary = run_matching(db, vehicle_id=vehicle.id, user_id=vehicle.user_id, now=now)
    db.commit()
    assert summary.backtests_written == 1
    row = next(r for r in predictions(db, vehicle, "leg") if r.observed_trip_id == a.id)
    assert (row.observed_energy_kwh, row.error_kwh) == (Decimal("4.000"), Decimal("2.000"))


def test_no_baseline_means_no_backtests(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    vehicle.make, vehicle.model = "Unknown", "Car"
    db.add(VehicleSpecs(vehicle_id=vehicle.id, usable_capacity_kwh=Decimal("60.00")))
    db.commit()
    add_readings(db, vehicle, TWO_LEGS)
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    infer(db, vehicle, now=now)
    assert len(vehicle_legs(db, vehicle)) == 2
    assert predictions(db, vehicle, "leg") == []
    summary = run_matching(db, vehicle_id=vehicle.id, user_id=vehicle.user_id, now=now)
    assert summary.backtests_skipped_no_baseline == 2


def test_someone_elses_vehicle_is_a_no_op(db: Session, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    store_fixture(db, vehicle, "example3_road_trip_dc_stop")
    add_plan(db, vehicle)
    summary = run_matching(db, vehicle_id=vehicle.id, user_id=uuid.uuid4(), now=EX3_NOW)
    assert summary.plans_matched == 0
