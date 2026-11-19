"""Observed trips: inference runs, review and reads (Phase 5 F3).

Turns stored telemetry into ``observed_trips`` rows (one per driving leg,
phase5_plan.md D1) with the pure engine in ``inference.py``, and backs the
``/observed-trips`` routes (``docs/api-contracts.md`` §3.18-3.18d).

When it runs (D2, ADR 0009):

- automatically, through ``on_telemetry_ingested``, which the webhook router
  runs in the background after a delivery that stored ``odometer``, ``soc`` or
  ``charging_status`` readings;
- on demand, through ``POST /observed-trips/recompute`` and
  ``scripts/infer_trips.py`` (catch-up paths).

Every run for one vehicle holds a Postgres advisory lock for the length of its
transaction, so runs are serialized across processes and instances, and every
run is idempotent: re-running with no new telemetry changes no row.

Confirmed and rejected legs are frozen (D3): inference never changes or
deletes them, and skips any leg whose odometer range overlaps one.

Every lookup is scoped to the caller: a leg or vehicle that isn't theirs is
``NOT_FOUND`` (engineering-guidelines §Security). Logs carry counts only.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from fastapi import status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, and_, case, delete, func, or_, select, text, true
from sqlalchemy.orm import Session, aliased

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.modules.trips.inference import (
    InferenceSettings,
    InferredLeg,
    Reading,
    SignalKind,
    infer_legs,
)
from app.modules.trips.observed_models import (
    FROZEN_REVIEW_STATUSES,
    ObservedTrip,
    PredictionResult,
)
from app.modules.vehicles.ingest_hooks import SessionOpener, TelemetryIngested
from app.modules.vehicles.models import Vehicle, VehicleSpecs
from app.modules.vehicles.spec_catalog import lookup_spec
from app.modules.vehicles.telemetry_service import TelemetryReading, list_readings

logger = logging.getLogger(__name__)

INFERENCE_VERSION = "telemetry-v1"
# Signals that can change a leg (D2). Anything else (range, …) doesn't trigger a run.
TRIGGER_CODES = frozenset({"odometer", "soc", "charging_status"})
# First key of the two-key advisory lock, so these locks can't collide with
# another feature's. The second key is a hash of the vehicle ID.
_LOCK_NAMESPACE = 5003

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

ReviewStatus = Literal["auto", "needs_review", "confirmed", "rejected"]
QualityFlag = Literal["high", "medium", "low"]
CapacitySource = Literal["vehicle", "catalog"]

_SIGNAL_KIND: dict[str, SignalKind] = {
    "odometer": "odometer_km",
    "soc": "soc_percent",
    "charging_status": "is_charging",
}
_CENT = Decimal("0.01")
_MILLI = Decimal("0.001")


# ---------------------------------------------------------------------------
# API shapes (§3.18-3.18d)
# ---------------------------------------------------------------------------


class ObservedTripOut(BaseModel):
    """One driving leg (§3.18 item, §3.18a)."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    start_time: datetime
    start_time_latest: datetime
    end_time: datetime
    start_time_is_exact: bool
    distance_km: float
    odometer_start_km: float
    odometer_end_km: float
    soc_start_percent: float | None
    soc_start_recorded_at: datetime | None
    soc_end_percent: float | None
    soc_end_recorded_at: datetime | None
    energy_kwh: float | None
    energy_kwh_uncertainty: float | None
    energy_source: Literal["soc_inferred", "manual"] | None
    usable_capacity_kwh: float | None
    capacity_source: CapacitySource | None
    quality_flag: QualityFlag
    quality_reasons: list[str]
    review_status: ReviewStatus
    reviewed_at: datetime | None
    in_progress: bool
    planned_trip_id: uuid.UUID | None
    plan_leg_index: int | None
    inference_version: str
    is_simulated: bool


class ObservedTripList(BaseModel):
    items: list[ObservedTripOut]
    next_cursor: str | None


class RecomputeRequest(BaseModel):
    vehicle_id: uuid.UUID
    full: bool = False


class RecomputeResponse(BaseModel):
    vehicle_id: uuid.UUID
    legs_created: int
    legs_updated: int
    legs_deleted: int
    legs_unchanged: int
    plans_matched: int
    dropped_simulated_readings: int


@dataclass(frozen=True)
class InferRunSummary:
    vehicle_id: uuid.UUID
    legs_created: int = 0
    legs_updated: int = 0
    legs_deleted: int = 0
    legs_unchanged: int = 0
    # Legs inference produced but didn't write because they overlap a frozen leg.
    legs_skipped_frozen: int = 0
    # Backtest rows removed because their leg's energy or distance changed.
    backtests_removed: int = 0
    plans_matched: int = 0
    dropped_simulated_readings: int = 0
    # Where this run started re-inferring; None = the whole history.
    window_start: datetime | None = None

    def response(self) -> RecomputeResponse:
        return RecomputeResponse(
            vehicle_id=self.vehicle_id,
            legs_created=self.legs_created,
            legs_updated=self.legs_updated,
            legs_deleted=self.legs_deleted,
            legs_unchanged=self.legs_unchanged,
            plans_matched=self.plans_matched,
            dropped_simulated_readings=self.dropped_simulated_readings,
        )


@dataclass(frozen=True)
class ObservedTripPage:
    items: list[ObservedTripOut]
    next_cursor: str | None


def inference_settings(settings: Settings) -> InferenceSettings:
    """The engine's settings from the config block "trips"."""

    return InferenceSettings(
        stop_split_minutes=settings.trips_stop_split_minutes,
        min_move_km=settings.trips_min_move_km,
        min_leg_km=settings.trips_min_leg_km,
        high_min_soc_delta=settings.trips_high_min_soc_delta,
        medium_min_soc_delta=settings.trips_medium_min_soc_delta,
        high_max_soc_age_minutes=settings.trips_high_max_soc_age_minutes,
        medium_max_soc_age_minutes=settings.trips_medium_max_soc_age_minutes,
        soc_resolution_points=settings.trips_soc_resolution_points,
        soc_increase_tolerance_points=settings.trips_soc_increase_tolerance_points,
    )


# ---------------------------------------------------------------------------
# Post-ingest hook (ADR 0009)
# ---------------------------------------------------------------------------


def on_telemetry_ingested(event: TelemetryIngested, open_session: SessionOpener) -> None:
    """Re-infer the vehicle's legs after a webhook stored new readings.

    Runs after the webhook response, in its own session. Exceptions propagate
    to ``run_ingest_hooks``, which logs them; Smartcar never sees them.
    """

    if not event.signal_codes & TRIGGER_CODES:
        return
    with open_session() as db:
        try:
            summary = run_for_vehicle(
                db,
                vehicle_id=event.vehicle_id,
                user_id=event.user_id,
                since=event.earliest_recorded_at,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    logger.info(
        "observed_trips_inferred",
        extra={
            "trigger": "webhook",
            "legs_created": summary.legs_created,
            "legs_updated": summary.legs_updated,
            "legs_deleted": summary.legs_deleted,
            "legs_unchanged": summary.legs_unchanged,
            "legs_skipped_frozen": summary.legs_skipped_frozen,
        },
    )


# ---------------------------------------------------------------------------
# Inference runs
# ---------------------------------------------------------------------------


def lock_vehicle(db: Session, vehicle_id: uuid.UUID) -> None:
    """Serialize inference and review for one vehicle until the transaction ends.

    Waits rather than skipping, so a run never misses rows committed just
    before it.
    """

    db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:vehicle_id))"),
        {"ns": _LOCK_NAMESPACE, "vehicle_id": str(vehicle_id)},
    )


def run_for_vehicle(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    user_id: uuid.UUID,
    since: datetime | None = None,
    full: bool = False,
    now: datetime | None = None,
    settings: InferenceSettings | None = None,
) -> InferRunSummary:
    """Lock, infer, then match plans. Flushes; the caller commits.

    ``NOT_FOUND`` if the vehicle isn't ``user_id``'s.
    """

    current = now or datetime.now(UTC)
    lock_vehicle(db, vehicle_id)
    summary = infer_for_vehicle(
        db,
        vehicle_id=vehicle_id,
        user_id=user_id,
        since=since,
        full=full,
        now=current,
        settings=settings,
    )
    plans_matched = _match_and_score_vehicle(
        db, vehicle_id=vehicle_id, user_id=user_id, now=current
    )
    return replace(summary, plans_matched=plans_matched)


def infer_for_vehicle(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    user_id: uuid.UUID,
    since: datetime | None = None,
    full: bool = False,
    now: datetime | None = None,
    settings: InferenceSettings | None = None,
) -> InferRunSummary:
    """Rebuild the vehicle's non-frozen legs from the window start onward.

    The caller holds ``lock_vehicle``. Flushes; the caller commits.
    ``since`` is the oldest reading a delivery inserted (late telemetry moves
    the window back); ``full`` rebuilds the whole history.
    """

    current = now or datetime.now(UTC)
    engine_settings = settings or inference_settings(get_settings())
    vehicle = db.scalar(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == user_id))
    if vehicle is None:
        raise _vehicle_not_found()

    window_start = _window_start(db, vehicle_id=vehicle_id, since=since, full=full)
    readings = [
        _engine_reading(r) for r in list_readings(db, vehicle_id=vehicle_id, since=window_start)
    ]
    capacity, capacity_source = _resolve_capacity(db, vehicle)
    result = infer_legs(
        readings,
        vehicle_is_simulated=vehicle.is_simulated,
        usable_capacity_kwh=float(capacity) if capacity is not None else None,
        now=current,
        settings=engine_settings,
    )

    # A leg that starts before the window is only a partial view of a leg the
    # window doesn't cover (the engine saw just its tail); never write it.
    produced = [
        leg for leg in result.legs if window_start is None or leg.start_time >= window_start
    ]

    frozen_ranges = [
        (row.odometer_start_km, row.odometer_end_km)
        for row in db.execute(
            select(ObservedTrip.odometer_start_km, ObservedTrip.odometer_end_km).where(
                ObservedTrip.vehicle_id == vehicle_id,
                ObservedTrip.review_status.in_(FROZEN_REVIEW_STATUSES),
            )
        )
    ]

    values_by_key: dict[Decimal, dict[str, Any]] = {}
    skipped_frozen = 0
    for leg in produced:
        values = _leg_values(
            leg,
            capacity=capacity,
            capacity_source=capacity_source,
            is_simulated=vehicle.is_simulated,
        )
        if values is None:
            continue
        start, end = values["odometer_start_km"], values["odometer_end_km"]
        if any(start < f_end and f_start < end for f_start, f_end in frozen_ranges):
            skipped_frozen += 1
            continue
        values_by_key[start] = values

    not_frozen = ObservedTrip.review_status.not_in(FROZEN_REVIEW_STATUSES)
    in_window: ColumnElement[bool] = (
        ObservedTrip.start_time >= window_start if window_start is not None else true()
    )
    existing = list(
        db.scalars(
            select(ObservedTrip).where(
                ObservedTrip.vehicle_id == vehicle_id,
                not_frozen,
                or_(in_window, ObservedTrip.odometer_start_km.in_(list(values_by_key))),
            )
        )
    )
    existing_by_key = {row.odometer_start_km: row for row in existing}

    created = updated = unchanged = deleted = 0
    changed_numbers: list[uuid.UUID] = []
    for key, values in values_by_key.items():
        row = existing_by_key.get(key)
        if row is None:
            db.add(ObservedTrip(user_id=vehicle.user_id, vehicle_id=vehicle_id, **values))
            created += 1
            continue
        changes = {k: v for k, v in values.items() if getattr(row, k) != v}
        if not changes:
            unchanged += 1
            continue
        if "energy_kwh" in changes or "distance_km" in changes:
            changed_numbers.append(row.id)
        for k, v in changes.items():
            setattr(row, k, v)
        updated += 1

    for row in existing:
        if row.odometer_start_km in values_by_key:
            continue
        if window_start is None or row.start_time >= window_start:
            db.delete(row)
            deleted += 1

    backtests_removed = 0
    if changed_numbers:
        # F4 writes a fresh backtest for these legs on its next pass (D9).
        removed = db.execute(
            delete(PredictionResult)
            .where(
                PredictionResult.kind == "leg",
                PredictionResult.observed_trip_id.in_(changed_numbers),
            )
            .returning(PredictionResult.id)
            .execution_options(synchronize_session=False)
        )
        backtests_removed = len(removed.scalars().all())
    db.flush()

    return InferRunSummary(
        vehicle_id=vehicle_id,
        legs_created=created,
        legs_updated=updated,
        legs_deleted=deleted,
        legs_unchanged=unchanged,
        legs_skipped_frozen=skipped_frozen,
        backtests_removed=backtests_removed,
        dropped_simulated_readings=result.dropped_simulated,
        window_start=window_start,
    )


def _match_and_score_vehicle(
    db: Session, *, vehicle_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> int:
    """P5-F4 call site: plan matching and backtests (phase5_plan.md §3 F4).

    Placeholder until F4 lands; it replaces this body with
    ``matching.match_and_score_vehicle(db, vehicle_id=..., user_id=..., now=...)``
    and returns the number of plans matched.
    """

    return 0


def _window_start(
    db: Session, *, vehicle_id: uuid.UUID, since: datetime | None, full: bool
) -> datetime | None:
    """Where re-inference starts (phase5_plan.md §3 F3); ``None`` = all history."""

    if full:
        return None
    legs = db.execute(
        select(
            ObservedTrip.start_time,
            ObservedTrip.end_time,
            ObservedTrip.closed,
            ObservedTrip.review_status,
        ).where(ObservedTrip.vehicle_id == vehicle_id)
    ).all()
    closed_ends = [leg.end_time for leg in legs if leg.closed]
    if not closed_ends:
        # No finished leg to anchor on (none yet, or only open ones): an open
        # leg must be able to grow from its own start, so rebuild everything.
        return None
    anchor: datetime = max(closed_ends)
    if since is None or since >= anchor:
        return anchor
    # Late telemetry: rebuild from the earliest non-frozen leg it can touch.
    reaching = [
        leg.start_time
        for leg in legs
        if leg.review_status not in FROZEN_REVIEW_STATUSES and leg.end_time >= since
    ]
    return min(reaching, default=since)


def _engine_reading(reading: TelemetryReading) -> Reading:
    return Reading(
        recorded_at=reading.recorded_at,
        signal=_SIGNAL_KIND[reading.code],
        value=reading.value,
        is_simulated=reading.is_simulated,
    )


def _resolve_capacity(
    db: Session, vehicle: Vehicle
) -> tuple[Decimal | None, CapacitySource | None]:
    """Specs row value if set, else the catalog value (D10, phase4_plan D14).

    A row value equal to the catalog's is reported as ``catalog``, the same
    rule as the planner: connecting a catalog vehicle copies the catalog into
    its row.
    """

    row = db.scalar(select(VehicleSpecs).where(VehicleSpecs.vehicle_id == vehicle.id))
    entry = lookup_spec(vehicle.make, vehicle.model, vehicle.year)
    catalog = (
        _q(entry.usable_capacity_kwh, _CENT)
        if entry is not None and entry.usable_capacity_kwh is not None
        else None
    )
    own = _q(row.usable_capacity_kwh, _CENT) if row is not None else None
    if own is not None and own > 0:
        return own, ("catalog" if own == catalog else "vehicle")
    if catalog is not None and catalog > 0:
        return catalog, "catalog"
    return None, None


def _leg_values(
    leg: InferredLeg,
    *,
    capacity: Decimal | None,
    capacity_source: CapacitySource | None,
    is_simulated: bool,
) -> dict[str, Any] | None:
    """Column values for ``leg``, rounded to the column scales; ``None`` if unusable."""

    odo_start = _q(leg.odometer_start_km, _CENT)
    odo_end = _q(leg.odometer_end_km, _CENT)
    assert odo_start is not None and odo_end is not None
    distance = odo_end - odo_start
    if distance <= 0:
        return None
    energy = _q(leg.energy_kwh, _MILLI)
    return {
        "start_time": leg.start_time,
        "start_time_latest": leg.start_time_latest,
        "end_time": leg.end_time,
        "odometer_start_km": odo_start,
        "odometer_end_km": odo_end,
        "distance_km": distance,
        "soc_start_percent": _q(leg.soc_start_percent, _CENT),
        "soc_start_recorded_at": leg.soc_start_recorded_at,
        "soc_end_percent": _q(leg.soc_end_percent, _CENT),
        "soc_end_recorded_at": leg.soc_end_recorded_at,
        "usable_capacity_kwh": capacity,
        "capacity_source": capacity_source if capacity is not None else None,
        "energy_kwh": energy,
        "energy_kwh_uncertainty": _q(leg.energy_kwh_uncertainty, _MILLI),
        "energy_source": "soc_inferred" if energy is not None else None,
        "quality_flag": leg.quality_flag,
        "quality_reasons": list(leg.quality_reasons),
        "review_status": "needs_review" if leg.quality_flag == "low" else "auto",
        "closed": leg.closed,
        "inference_version": INFERENCE_VERSION,
        "is_simulated": is_simulated,
    }


def _q(value: float | Decimal | None, step: Decimal) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Review (§3.18c-d)
# ---------------------------------------------------------------------------


def confirm(
    db: Session, *, user_id: uuid.UUID, trip_id: uuid.UUID, now: datetime | None = None
) -> ObservedTripOut:
    """Mark a leg as a real trip of the caller's. Freezes it. Flushes."""

    return _review(db, user_id=user_id, trip_id=trip_id, new_status="confirmed", now=now)


def reject(
    db: Session, *, user_id: uuid.UUID, trip_id: uuid.UUID, now: datetime | None = None
) -> ObservedTripOut:
    """Mark a leg as not the caller's trip. Freezes it and un-matches its plan. Flushes."""

    return _review(db, user_id=user_id, trip_id=trip_id, new_status="rejected", now=now)


def _review(
    db: Session,
    *,
    user_id: uuid.UUID,
    trip_id: uuid.UUID,
    new_status: Literal["confirmed", "rejected"],
    now: datetime | None,
) -> ObservedTripOut:
    current = now or datetime.now(UTC)
    vehicle_id = db.scalar(
        select(ObservedTrip.vehicle_id).where(
            ObservedTrip.id == trip_id, ObservedTrip.user_id == user_id
        )
    )
    if vehicle_id is None:
        raise _trip_not_found()
    # The same lock as inference, so a run can't overwrite a leg mid-review.
    lock_vehicle(db, vehicle_id)
    trip = db.scalar(
        select(ObservedTrip)
        .where(ObservedTrip.id == trip_id, ObservedTrip.user_id == user_id)
        .with_for_update()
    )
    if trip is None:
        raise _trip_not_found()

    if trip.review_status != new_status:
        trip.review_status = new_status
        trip.reviewed_at = current
    unmatched = False
    if new_status == "rejected" and trip.planned_trip_id is not None:
        trip.planned_trip_id = None
        unmatched = True
    db.flush()
    if unmatched:
        # F4 re-matches and re-scores the plan the leg belonged to.
        _match_and_score_vehicle(db, vehicle_id=vehicle_id, user_id=user_id, now=current)
    return get_observed_trip(db, user_id=user_id, trip_id=trip_id, now=current)


# ---------------------------------------------------------------------------
# Reads (§3.18, §3.18a)
# ---------------------------------------------------------------------------


def get_observed_trip(
    db: Session,
    *,
    user_id: uuid.UUID,
    trip_id: uuid.UUID,
    now: datetime | None = None,
    settings: InferenceSettings | None = None,
) -> ObservedTripOut:
    row = db.execute(
        _leg_query().where(ObservedTrip.id == trip_id, ObservedTrip.user_id == user_id)
    ).first()
    if row is None:
        raise _trip_not_found()
    return _out(row, now=now or datetime.now(UTC), settings=settings)


def list_observed_trips(
    db: Session,
    *,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID | None = None,
    review_statuses: Sequence[str] | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    now: datetime | None = None,
    settings: InferenceSettings | None = None,
) -> ObservedTripPage:
    """Newest first (``end_time`` desc, ties by ``id``); rejected hidden by default."""

    if vehicle_id is not None:
        ensure_vehicle_owned(db, user_id=user_id, vehicle_id=vehicle_id)
    query = _leg_query().where(ObservedTrip.user_id == user_id)
    if vehicle_id is not None:
        query = query.where(ObservedTrip.vehicle_id == vehicle_id)
    if review_statuses:
        query = query.where(ObservedTrip.review_status.in_(list(review_statuses)))
    else:
        query = query.where(ObservedTrip.review_status != "rejected")
    if from_time is not None:
        query = query.where(ObservedTrip.end_time >= from_time)
    if to_time is not None:
        query = query.where(ObservedTrip.end_time < to_time)
    if cursor is not None:
        after_end, after_id = decode_cursor(cursor)
        query = query.where(
            or_(
                ObservedTrip.end_time < after_end,
                and_(ObservedTrip.end_time == after_end, ObservedTrip.id > after_id),
            )
        )
    query = query.order_by(ObservedTrip.end_time.desc(), ObservedTrip.id.asc()).limit(limit + 1)

    current = now or datetime.now(UTC)
    rows = db.execute(query).all()
    items = [_out(row, now=current, settings=settings) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_cursor(end_time=last.end_time, trip_id=last.id)
    return ObservedTripPage(items=items, next_cursor=next_cursor)


def ensure_vehicle_owned(db: Session, *, user_id: uuid.UUID, vehicle_id: uuid.UUID) -> None:
    """``NOT_FOUND`` unless the caller owns the vehicle (connected or not)."""

    found = db.scalar(
        select(Vehicle.id).where(Vehicle.id == vehicle_id, Vehicle.user_id == user_id)
    )
    if found is None:
        raise _vehicle_not_found()


def _leg_query() -> Any:
    other = aliased(ObservedTrip)
    position = (
        select(func.count(other.id))
        .where(
            other.planned_trip_id == ObservedTrip.planned_trip_id,
            or_(
                other.start_time < ObservedTrip.start_time,
                and_(other.start_time == ObservedTrip.start_time, other.id <= ObservedTrip.id),
            ),
        )
        .scalar_subquery()
    )
    plan_leg_index = case((ObservedTrip.planned_trip_id.is_(None), None), else_=position)
    return select(ObservedTrip, plan_leg_index.label("plan_leg_index"))


def _out(row: Any, *, now: datetime, settings: InferenceSettings | None) -> ObservedTripOut:
    trip: ObservedTrip = row[0]
    split = (settings or inference_settings(get_settings())).stop_split_minutes
    # D14, the same rule as ``inference.is_in_progress``.
    in_progress = not trip.closed and now - trip.end_time < timedelta(minutes=split)
    return ObservedTripOut(
        id=trip.id,
        vehicle_id=trip.vehicle_id,
        start_time=trip.start_time,
        start_time_latest=trip.start_time_latest,
        end_time=trip.end_time,
        start_time_is_exact=trip.start_time == trip.start_time_latest,
        distance_km=float(trip.distance_km),
        odometer_start_km=float(trip.odometer_start_km),
        odometer_end_km=float(trip.odometer_end_km),
        soc_start_percent=_float(trip.soc_start_percent),
        soc_start_recorded_at=trip.soc_start_recorded_at,
        soc_end_percent=_float(trip.soc_end_percent),
        soc_end_recorded_at=trip.soc_end_recorded_at,
        energy_kwh=_float(trip.energy_kwh),
        energy_kwh_uncertainty=_float(trip.energy_kwh_uncertainty),
        energy_source=trip.energy_source,  # type: ignore[arg-type]
        usable_capacity_kwh=_float(trip.usable_capacity_kwh),
        capacity_source=trip.capacity_source,  # type: ignore[arg-type]
        quality_flag=trip.quality_flag,  # type: ignore[arg-type]
        quality_reasons=list(trip.quality_reasons or []),
        review_status=trip.review_status,  # type: ignore[arg-type]
        reviewed_at=trip.reviewed_at,
        in_progress=in_progress,
        planned_trip_id=trip.planned_trip_id,
        plan_leg_index=row.plan_leg_index,
        inference_version=trip.inference_version,
        is_simulated=trip.is_simulated,
    )


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def encode_cursor(*, end_time: datetime, trip_id: uuid.UUID) -> str:
    body = json.dumps({"k": end_time.isoformat(), "i": str(trip_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """``(end_time, id)`` from ``cursor``; anything invalid is a 422."""

    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise _invalid_cursor() from None
    if not isinstance(data, dict):
        raise _invalid_cursor()
    raw_key, raw_id = data.get("k"), data.get("i")
    if not (isinstance(raw_key, str) and isinstance(raw_id, str)):
        raise _invalid_cursor()
    try:
        key = datetime.fromisoformat(raw_key)
        trip_id = uuid.UUID(raw_id)
    except ValueError:
        raise _invalid_cursor() from None
    if key.tzinfo is None:
        raise _invalid_cursor()
    return key, trip_id


def _invalid_cursor() -> ApiError:
    return ApiError(
        "VALIDATION_ERROR",
        "Invalid cursor; list the observed trips again without it.",
        status_code=422,
        details={"reason": "INVALID_CURSOR"},
    )


def _trip_not_found() -> ApiError:
    return ApiError("NOT_FOUND", "Observed trip not found.", status_code=status.HTTP_404_NOT_FOUND)


def _vehicle_not_found() -> ApiError:
    return ApiError("NOT_FOUND", "Vehicle not found.", status_code=status.HTTP_404_NOT_FOUND)


__all__ = [
    "DEFAULT_LIMIT",
    "INFERENCE_VERSION",
    "MAX_LIMIT",
    "TRIGGER_CODES",
    "InferRunSummary",
    "ObservedTripList",
    "ObservedTripOut",
    "ObservedTripPage",
    "RecomputeRequest",
    "RecomputeResponse",
    "confirm",
    "decode_cursor",
    "encode_cursor",
    "ensure_vehicle_owned",
    "get_observed_trip",
    "infer_for_vehicle",
    "inference_settings",
    "list_observed_trips",
    "lock_vehicle",
    "on_telemetry_ingested",
    "reject",
    "run_for_vehicle",
]
