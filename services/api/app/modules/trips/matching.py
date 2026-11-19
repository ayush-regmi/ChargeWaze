"""Plan ↔ observation matching and backtests (Phase 5 F4).

Design: ``docs/phase5_plan.md`` D4 (matching), D9 (backtests) and §4.2 (the
interface and the algorithm). Contract: ``docs/api-contracts.md`` §3.18e.

Two layers:

- a **pure core** (``match_plans``, ``score_plan``, ``chain_fits``,
  ``backtest_leg``) over frozen dataclasses, with no database and no clock;
- ``match_and_score_vehicle``, the one database entry point, called by
  ``observed_service`` after every inference run and after a reject that
  removed a plan link. It runs under the caller's per-vehicle advisory lock,
  flushes, and never commits.

We have no location (D7), so a plan is matched to a *chain* of consecutive
driving legs by time and odometer distance only. A plan matches at most one
chain and a leg belongs to at most one plan.
"""

from __future__ import annotations

import itertools
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.trips.models import PlannedTrip
from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.vehicles.models import Vehicle, VehicleSpecs
from app.modules.vehicles.spec_catalog import lookup_spec

# The prediction model being judged (phase5_plan D9): the flat baseline.
MODEL_VERSION = "deterministic-v1"
# Plans and legs older than this are not matched (§3 F4 "Candidates").
CANDIDATE_DAYS = 7

_CENT = Decimal("0.01")
_MILLI = Decimal("0.001")


# ---------------------------------------------------------------------------
# Pure core (§4.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanCandidate:
    trip_id: uuid.UUID
    created_at: datetime
    departure_time: datetime | None
    saved: bool
    planned_distance_km: float  # route + sum of 2 * distance_from_route_km * road_factor
    predicted_energy_kwh: float  # totals.energy_kwh_estimated
    predicted_arrival_soc: float  # fraction
    starting_soc: float  # fraction


@dataclass(frozen=True)
class LegCandidate:
    leg_id: uuid.UUID
    start_time: datetime
    start_time_latest: datetime
    end_time: datetime
    distance_km: float
    energy_kwh: float | None
    soc_start_percent: float | None
    soc_end_percent: float | None


@dataclass(frozen=True)
class MatchSettings:
    window_before_created_minutes: float = 15
    window_after_created_hours: float = 24
    departure_tolerance_minutes: float = 120
    max_gap_minutes: float = 120
    distance_tolerance_fraction: float = 0.10
    distance_tolerance_min_km: float = 5


@dataclass(frozen=True)
class PlanMatch:
    trip_id: uuid.UUID
    leg_ids: tuple[uuid.UUID, ...]  # in drive order; the API's plan_leg_index is this order
    observed_distance_km: float
    distance_error_km: float  # observed - planned


@dataclass(frozen=True)
class PlanScore:
    observed_energy_kwh: float | None
    error_kwh: float | None
    observed_start_soc: float | None
    observed_arrival_soc: float | None
    arrival_soc_error_points: float | None


def plan_window(
    plan: PlanCandidate, settings: MatchSettings = MatchSettings()
) -> tuple[datetime, datetime]:
    """When the drive may start: ``departure_time`` ± tolerance, else around ``created_at``."""

    if plan.departure_time is not None:
        tolerance = timedelta(minutes=settings.departure_tolerance_minutes)
        return plan.departure_time - tolerance, plan.departure_time + tolerance
    return (
        plan.created_at - timedelta(minutes=settings.window_before_created_minutes),
        plan.created_at + timedelta(hours=settings.window_after_created_hours),
    )


def distance_tolerance_km(plan: PlanCandidate, settings: MatchSettings = MatchSettings()) -> float:
    return max(
        settings.distance_tolerance_min_km,
        settings.distance_tolerance_fraction * plan.planned_distance_km,
    )


def match_plans(
    plans: Sequence[PlanCandidate],
    legs: Sequence[LegCandidate],
    settings: MatchSettings = MatchSettings(),
) -> tuple[PlanMatch, ...]:
    """Match each plan to at most one chain of consecutive legs (§4.2 algorithm).

    ``legs`` are the free candidates (not in progress, not rejected, not
    matched yet). Returns matches ordered by the greedy pick order.
    """

    ordered = sorted(legs, key=lambda leg: (leg.start_time, leg.leg_id))
    max_gap = timedelta(minutes=settings.max_gap_minutes)

    # (relative error, preference..., first leg start, trip_id) → match
    candidates: list[tuple[tuple[Any, ...], PlanMatch]] = []
    for plan in plans:
        if not (math.isfinite(plan.planned_distance_km) and plan.planned_distance_km > 0):
            continue
        window_start, window_end = plan_window(plan, settings)
        tolerance = distance_tolerance_km(plan, settings)
        for i, first in enumerate(ordered):
            if not _overlaps(first, window_start, window_end):
                continue
            chain = _best_prefix(ordered, i, plan.planned_distance_km, tolerance, max_gap)
            if chain is None:
                continue
            observed = sum(leg.distance_km for leg in chain)
            error = observed - plan.planned_distance_km
            if abs(error) > tolerance:
                continue
            key = (
                abs(error) / plan.planned_distance_km,
                0 if plan.departure_time is not None else 1,
                0 if plan.saved else 1,
                -plan.created_at.timestamp(),
                first.start_time,
                str(plan.trip_id),
            )
            candidates.append(
                (
                    key,
                    PlanMatch(
                        trip_id=plan.trip_id,
                        leg_ids=tuple(leg.leg_id for leg in chain),
                        observed_distance_km=observed,
                        distance_error_km=error,
                    ),
                )
            )

    candidates.sort(key=lambda item: item[0])
    taken_plans: set[uuid.UUID] = set()
    taken_legs: set[uuid.UUID] = set()
    result: list[PlanMatch] = []
    for _, match in candidates:
        if match.trip_id in taken_plans or taken_legs.intersection(match.leg_ids):
            continue
        taken_plans.add(match.trip_id)
        taken_legs.update(match.leg_ids)
        result.append(match)
    return tuple(result)


def chain_fits(
    plan: PlanCandidate, chain: Sequence[LegCandidate], settings: MatchSettings = MatchSettings()
) -> bool:
    """Whether an existing match still holds: the same rules ``match_plans`` applies."""

    if not chain:
        return False
    ordered = sorted(chain, key=lambda leg: (leg.start_time, leg.leg_id))
    window_start, window_end = plan_window(plan, settings)
    if not _overlaps(ordered[0], window_start, window_end):
        return False
    max_gap = timedelta(minutes=settings.max_gap_minutes)
    if any(b.start_time - a.end_time > max_gap for a, b in itertools.pairwise(ordered)):
        return False
    observed = sum(leg.distance_km for leg in ordered)
    return abs(observed - plan.planned_distance_km) <= distance_tolerance_km(plan, settings)


def score_plan(plan: PlanCandidate, chain: Sequence[LegCandidate]) -> PlanScore:
    """Plan prediction vs the chain that carried it out (§3 F4 "Plan scoring")."""

    ordered = sorted(chain, key=lambda leg: (leg.start_time, leg.leg_id))
    energies = [leg.energy_kwh for leg in ordered]
    observed_energy = (
        sum(e for e in energies if e is not None)
        if ordered and all(e is not None for e in energies)
        else None
    )
    first_soc = ordered[0].soc_start_percent if ordered else None
    last_soc = ordered[-1].soc_end_percent if ordered else None
    observed_start = first_soc / 100 if first_soc is not None else None
    observed_arrival = last_soc / 100 if last_soc is not None else None
    return PlanScore(
        observed_energy_kwh=observed_energy,
        error_kwh=(
            observed_energy - plan.predicted_energy_kwh if observed_energy is not None else None
        ),
        observed_start_soc=observed_start,
        observed_arrival_soc=observed_arrival,
        arrival_soc_error_points=(
            (observed_arrival - plan.predicted_arrival_soc) * 100
            if observed_arrival is not None
            else None
        ),
    )


def backtest_leg(baseline_kwh_per_km: float, distance_km: float) -> float:
    """Predicted kWh for a leg: the flat baseline (phase4_plan D1).

    The same formula as ``energy.model.segment_energy_kwh``. It takes the
    baseline rather than an ``EnergyVehicle`` because that type requires a
    charging curve, which a backtest doesn't use (F4 As built).
    """

    if not (math.isfinite(distance_km) and distance_km >= 0):
        raise ValueError("distance_km must be a finite number >= 0.")
    if not (math.isfinite(baseline_kwh_per_km) and baseline_kwh_per_km > 0):
        raise ValueError("baseline_kwh_per_km must be a finite number > 0.")
    return distance_km * baseline_kwh_per_km


def planned_distance_km(payload: Mapping[str, Any]) -> float | None:
    """Route distance plus every stop's round-trip detour, from a stored plan body."""

    route = payload.get("route")
    if not isinstance(route, Mapping) or route.get("distance_km") is None:
        return None
    assumptions = payload.get("assumptions") or {}
    factor = float(assumptions.get("detour_road_factor") or 0.0)
    detours = sum(
        2 * float(stop.get("distance_from_route_km") or 0.0) * factor
        for stop in payload.get("stops") or ()
    )
    return float(route["distance_km"]) + detours


def _overlaps(leg: LegCandidate, start: datetime, end: datetime) -> bool:
    return leg.start_time <= end and leg.start_time_latest >= start


def _best_prefix(
    ordered: Sequence[LegCandidate],
    first: int,
    planned_km: float,
    tolerance_km: float,
    max_gap: timedelta,
) -> list[LegCandidate] | None:
    """The prefix starting at ``first`` whose distance is closest to ``planned_km``.

    Extends while each gap is ≤ ``max_gap`` and the sum stays ≤ planned +
    tolerance. Ties keep the shorter chain.
    """

    chain: list[LegCandidate] = []
    best: list[LegCandidate] | None = None
    best_error = math.inf
    total = 0.0
    for leg in ordered[first:]:
        if chain and leg.start_time - chain[-1].end_time > max_gap:
            break
        if total + leg.distance_km > planned_km + tolerance_km:
            break
        chain.append(leg)
        total += leg.distance_km
        error = abs(total - planned_km)
        if error < best_error:
            best, best_error = list(chain), error
    return best


# ---------------------------------------------------------------------------
# Database entry point
# ---------------------------------------------------------------------------


@dataclass
class MatchRunSummary:
    plans_matched: int = 0
    plans_unmatched: int = 0
    plans_rescored: int = 0
    backtests_written: int = 0
    # Finished legs with energy that got no backtest because the vehicle has no baseline.
    backtests_skipped_no_baseline: int = 0
    matched_plan_ids: list[uuid.UUID] = field(default_factory=list)


def match_and_score_vehicle(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
    settings: MatchSettings = MatchSettings(),
) -> int:
    """Re-check matched plans, match new ones, write backtests. Returns plans matched."""

    return run_matching(
        db, vehicle_id=vehicle_id, user_id=user_id, now=now, settings=settings
    ).plans_matched


def run_matching(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
    settings: MatchSettings = MatchSettings(),
) -> MatchRunSummary:
    """``match_and_score_vehicle`` with the full summary (for tests and logs).

    The caller holds ``observed_service.lock_vehicle``. Flushes; never commits.
    A vehicle that isn't ``user_id``'s is a no-op.
    """

    summary = MatchRunSummary()
    vehicle = db.scalar(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == user_id))
    if vehicle is None:
        return summary
    split = timedelta(minutes=get_settings().trips_stop_split_minutes)

    _recheck_completed(db, vehicle, now=now, split=split, settings=settings, summary=summary)
    db.flush()
    _match_new(db, vehicle, now=now, split=split, settings=settings, summary=summary)
    db.flush()
    _write_backtests(db, vehicle, now=now, split=split, summary=summary)
    db.flush()
    return summary


def _in_progress(leg: ObservedTrip, *, now: datetime, split: timedelta) -> bool:
    """D14, the same rule as ``inference.is_in_progress``."""

    return not leg.closed and now - leg.end_time < split


def _leg_candidate(leg: ObservedTrip) -> LegCandidate:
    return LegCandidate(
        leg_id=leg.id,
        start_time=leg.start_time,
        start_time_latest=leg.start_time_latest,
        end_time=leg.end_time,
        distance_km=float(leg.distance_km),
        energy_kwh=_float(leg.energy_kwh),
        soc_start_percent=_float(leg.soc_start_percent),
        soc_end_percent=_float(leg.soc_end_percent),
    )


def _plan_candidate(trip: PlannedTrip) -> PlanCandidate | None:
    """``None`` when the stored body lacks what scoring needs (an infeasible plan)."""

    payload = trip.plan_payload or {}
    distance = planned_distance_km(payload)
    totals = payload.get("totals")
    arrival = payload.get("arrival_soc")
    starting = payload.get("starting_soc")
    if (
        distance is None
        or not isinstance(totals, Mapping)
        or totals.get("energy_kwh_estimated") is None
        or arrival is None
        or starting is None
    ):
        return None
    return PlanCandidate(
        trip_id=trip.id,
        created_at=trip.created_at,
        departure_time=trip.departure_time,
        saved=trip.saved_at is not None,
        planned_distance_km=distance,
        predicted_energy_kwh=float(totals["energy_kwh_estimated"]),
        predicted_arrival_soc=float(arrival),
        starting_soc=float(starting),
    )


def _recheck_completed(
    db: Session,
    vehicle: Vehicle,
    *,
    now: datetime,
    split: timedelta,
    settings: MatchSettings,
    summary: MatchRunSummary,
) -> None:
    """Re-score every matched plan, or un-match it if its legs no longer fit.

    A leg can be rejected (its link is cleared), deleted or rebuilt by
    inference after the match. Only ``completed`` plans are checked: matching
    is the only thing that sets that status.
    """

    plans = db.scalars(
        select(PlannedTrip).where(
            PlannedTrip.vehicle_id == vehicle.id, PlannedTrip.status == "completed"
        )
    ).all()
    for trip in plans:
        legs = list(
            db.scalars(
                select(ObservedTrip)
                .where(ObservedTrip.planned_trip_id == trip.id)
                .order_by(ObservedTrip.start_time, ObservedTrip.id)
            )
        )
        candidate = _plan_candidate(trip)
        if candidate is None:
            continue
        chain = [_leg_candidate(leg) for leg in legs]
        still_fits = (
            bool(legs)
            and all(
                leg.review_status != "rejected" and not _in_progress(leg, now=now, split=split)
                for leg in legs
            )
            and chain_fits(candidate, chain, settings)
        )
        if still_fits:
            if _write_plan_row(db, vehicle, trip, candidate, chain, now=now):
                summary.plans_rescored += 1
            continue
        for leg in legs:
            leg.planned_trip_id = None
        trip.status = "planned"
        row = _plan_row(db, trip.id)
        if row is not None:
            db.delete(row)
        summary.plans_unmatched += 1


def _match_new(
    db: Session,
    vehicle: Vehicle,
    *,
    now: datetime,
    split: timedelta,
    settings: MatchSettings,
    summary: MatchRunSummary,
) -> None:
    since = now - timedelta(days=CANDIDATE_DAYS)
    has_legs = exists().where(ObservedTrip.planned_trip_id == PlannedTrip.id)
    trips = db.scalars(
        select(PlannedTrip).where(
            PlannedTrip.vehicle_id == vehicle.id,
            PlannedTrip.status == "planned",
            PlannedTrip.plan_status == "feasible",
            PlannedTrip.created_at >= since,
            ~has_legs,
        )
    ).all()
    plans = {c.trip_id: c for c in (_plan_candidate(t) for t in trips) if c is not None}
    if not plans:
        return
    rows = db.scalars(
        select(ObservedTrip).where(
            and_(
                ObservedTrip.vehicle_id == vehicle.id,
                ObservedTrip.review_status != "rejected",
                ObservedTrip.planned_trip_id.is_(None),
                ObservedTrip.end_time >= since,
            )
        )
    ).all()
    legs = {row.id: row for row in rows if not _in_progress(row, now=now, split=split)}
    if not legs:
        return
    trips_by_id = {t.id: t for t in trips}
    for match in match_plans(
        list(plans.values()), [_leg_candidate(r) for r in legs.values()], settings
    ):
        trip = trips_by_id[match.trip_id]
        chain_rows = [legs[leg_id] for leg_id in match.leg_ids]
        for row in chain_rows:
            row.planned_trip_id = trip.id
        trip.status = "completed"
        _write_plan_row(
            db,
            vehicle,
            trip,
            plans[trip.id],
            [_leg_candidate(r) for r in chain_rows],
            now=now,
        )
        summary.plans_matched += 1
        summary.matched_plan_ids.append(trip.id)


def _plan_row(db: Session, trip_id: uuid.UUID) -> PredictionResult | None:
    return db.scalar(
        select(PredictionResult).where(
            PredictionResult.kind == "plan",
            PredictionResult.planned_trip_id == trip_id,
            PredictionResult.model_version == MODEL_VERSION,
        )
    )


def _write_plan_row(
    db: Session,
    vehicle: Vehicle,
    trip: PlannedTrip,
    plan: PlanCandidate,
    chain: Sequence[LegCandidate],
    *,
    now: datetime,
) -> bool:
    """Insert or update the plan's ``kind = 'plan'`` row. True if anything changed.

    ``created_at`` is the match time (the API's ``matched_at``); a re-score
    keeps it.
    """

    score = score_plan(plan, chain)
    values: dict[str, Any] = {
        "distance_km": _q(plan.planned_distance_km, _CENT),
        "observed_distance_km": _q(sum(leg.distance_km for leg in chain), _CENT),
        "predicted_energy_kwh": _q(plan.predicted_energy_kwh, _MILLI),
        "observed_energy_kwh": _q(score.observed_energy_kwh, _MILLI),
        "error_kwh": _q(score.error_kwh, _MILLI),
        "predicted_arrival_soc": _q(_soc(plan.predicted_arrival_soc), _MILLI),
        "observed_arrival_soc": _q(score.observed_arrival_soc, _MILLI),
        "observed_start_soc": _q(score.observed_start_soc, _MILLI),
        "arrival_soc_error_points": _q(score.arrival_soc_error_points, _CENT),
        "inputs": {"plan_model_version": trip.model_version},
        "is_simulated": vehicle.is_simulated,
    }
    return _upsert(
        db,
        _plan_row(db, trip.id),
        values,
        new=lambda: PredictionResult(
            user_id=vehicle.user_id,
            vehicle_id=vehicle.id,
            kind="plan",
            planned_trip_id=trip.id,
            model_version=MODEL_VERSION,
            created_at=now,
            updated_at=now,
        ),
    )


def _write_backtests(
    db: Session, vehicle: Vehicle, *, now: datetime, split: timedelta, summary: MatchRunSummary
) -> None:
    """A ``kind = 'leg'`` row for every finished leg with energy (D9).

    Missing rows are written; a row whose leg numbers or baseline changed is
    updated. Rows of legs that lost their energy are removed by inference
    (F3), and deleted legs lose theirs through the cascade.
    """

    legs = db.scalars(
        select(ObservedTrip).where(
            ObservedTrip.vehicle_id == vehicle.id, ObservedTrip.energy_kwh.is_not(None)
        )
    ).all()
    finished = [leg for leg in legs if not _in_progress(leg, now=now, split=split)]
    if not finished:
        return
    baseline, source = _resolve_baseline(db, vehicle)
    if baseline is None or source is None:
        summary.backtests_skipped_no_baseline += len(finished)
        return
    existing = {
        row.observed_trip_id: row
        for row in db.scalars(
            select(PredictionResult).where(
                PredictionResult.kind == "leg",
                PredictionResult.vehicle_id == vehicle.id,
                PredictionResult.model_version == MODEL_VERSION,
            )
        )
    }
    for leg in finished:
        assert leg.energy_kwh is not None
        predicted = _q(backtest_leg(float(baseline), float(leg.distance_km)), _MILLI)
        assert predicted is not None
        observed = leg.energy_kwh.quantize(_MILLI)
        values: dict[str, Any] = {
            "distance_km": leg.distance_km,
            "observed_distance_km": leg.distance_km,
            "predicted_energy_kwh": predicted,
            "observed_energy_kwh": observed,
            # On the rounded values, so error = energy - predicted exactly (F5 relies on it).
            "error_kwh": observed - predicted,
            "inputs": {
                "baseline_kwh_per_km": float(baseline),
                "baseline_source": source,
                "usable_capacity_kwh": _float(leg.usable_capacity_kwh),
            },
            "is_simulated": vehicle.is_simulated,
        }
        leg_id = leg.id
        if _upsert(
            db,
            existing.get(leg_id),
            values,
            new=lambda leg_id=leg_id: PredictionResult(
                user_id=vehicle.user_id,
                vehicle_id=vehicle.id,
                kind="leg",
                observed_trip_id=leg_id,
                model_version=MODEL_VERSION,
                created_at=now,
                updated_at=now,
            ),
        ):
            summary.backtests_written += 1


def _upsert(
    db: Session,
    row: PredictionResult | None,
    values: Mapping[str, Any],
    *,
    new: Any,
) -> bool:
    if row is None:
        created: PredictionResult = new()
        for key, value in values.items():
            setattr(created, key, value)
        db.add(created)
        return True
    changes = {k: v for k, v in values.items() if getattr(row, k) != v}
    for key, value in changes.items():
        setattr(row, key, value)
    return bool(changes)


def _resolve_baseline(
    db: Session, vehicle: Vehicle
) -> tuple[Decimal | None, Literal["vehicle", "catalog"] | None]:
    """Specs row value if set, else the catalog value (phase4_plan D14, P4-F8).

    The same rule as ``routing.planner_service.resolve_specs``, read here
    because ``routing`` imports ``trips`` (importing it back would be a cycle).
    Connecting never copies the catalog baseline into the row, so a row value
    is always the user's own.
    """

    row = db.scalar(select(VehicleSpecs).where(VehicleSpecs.vehicle_id == vehicle.id))
    if row is not None and row.baseline_kwh_per_km is not None and row.baseline_kwh_per_km > 0:
        return Decimal(row.baseline_kwh_per_km), "vehicle"
    entry = lookup_spec(vehicle.make, vehicle.model, vehicle.year)
    catalog = entry.baseline_kwh_per_km if entry is not None else None
    if catalog is not None and catalog > 0:
        return Decimal(catalog), "catalog"
    return None, None


def _soc(value: float) -> float:
    """Clamp a plan SoC into the column's 0..1 range (a plan never stores outside it)."""

    return min(1.0, max(0.0, value))


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _q(value: float | Decimal | None, step: Decimal) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP)


__all__ = [
    "CANDIDATE_DAYS",
    "MODEL_VERSION",
    "LegCandidate",
    "MatchRunSummary",
    "MatchSettings",
    "PlanCandidate",
    "PlanMatch",
    "PlanScore",
    "backtest_leg",
    "chain_fits",
    "distance_tolerance_km",
    "match_and_score_vehicle",
    "match_plans",
    "plan_window",
    "planned_distance_km",
    "run_matching",
    "score_plan",
]
