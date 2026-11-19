"""Read-only queries over observed legs and their predictions (Phase 5 F5).

The analytics module reads ``observed_trips``, ``prediction_results`` and the
matched plan's labels only through these functions, so it never queries
another module's tables (architecture.md §3; phase5_plan.md §3 F5). Nothing
here writes.

Every function filters on the caller (``user_id``) and one vehicle. Rejected
legs are never returned (contract §3.11a). Simulated legs are left out unless
``include_simulated`` (D11). Results are frozen dataclasses with floats in raw
units (km, kWh, percent) and aware UTC datetimes.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.trips.inference import InferenceSettings
from app.modules.trips.models import PlannedTrip
from app.modules.trips.observed_models import ObservedTrip, PredictionResult

# The deterministic baseline the per-leg backtests are written with (D9).
DETERMINISTIC_MODEL_VERSION = "deterministic-v1"

# D14: a leg that isn't closed stays "in progress" for this long after its
# last reading. The value is F2's inference default; the "trips" config block
# (F3) may later override it there.
IN_PROGRESS_WINDOW = timedelta(minutes=InferenceSettings().stop_split_minutes)


@dataclass(frozen=True)
class LegRange:
    """Which legs to read: one vehicle, ``end_time`` in ``[start, end)``."""

    user_id: uuid.UUID
    vehicle_id: uuid.UUID
    start: datetime  # aware, inclusive
    end: datetime  # aware, exclusive
    include_simulated: bool


@dataclass(frozen=True)
class AnalyticsLeg:
    id: uuid.UUID
    vehicle_id: uuid.UUID
    start_time: datetime
    start_time_latest: datetime
    end_time: datetime
    distance_km: float
    soc_start_percent: float | None
    soc_end_percent: float | None
    energy_kwh: float | None
    energy_kwh_uncertainty: float | None
    energy_source: str | None
    quality_flag: str
    quality_reasons: tuple[str, ...]
    review_status: str
    in_progress: bool
    planned_trip_id: uuid.UUID | None
    plan_origin_label: str | None
    plan_destination_label: str | None
    is_simulated: bool
    # The leg's deterministic backtest (D9); None when it has none.
    estimated_energy_kwh: float | None
    # observed - estimated; None unless both are known.
    error_kwh: float | None


@dataclass(frozen=True)
class PlanScore:
    """A ``kind = 'plan'`` prediction row whose chain ended in the range."""

    planned_trip_id: uuid.UUID
    predicted_energy_kwh: float
    observed_energy_kwh: float | None
    error_kwh: float | None
    arrival_soc_error_points: float | None


def list_legs(
    db: Session, rng: LegRange, *, now: datetime, model_version: str = DETERMINISTIC_MODEL_VERSION
) -> list[AnalyticsLeg]:
    """Every non-rejected leg in the range, newest first (``end_time`` desc, ``id``)."""

    query = _legs_query(rng, model_version).order_by(ObservedTrip.end_time.desc(), ObservedTrip.id)
    return [_to_leg(row, now=now) for row in db.execute(query).all()]


def page_legs(
    db: Session,
    rng: LegRange,
    *,
    now: datetime,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None = None,
    model_version: str = DETERMINISTIC_MODEL_VERSION,
) -> list[AnalyticsLeg]:
    """Up to ``limit`` legs in ``list_legs`` order, after the keyset ``(end_time, id)``."""

    query = _legs_query(rng, model_version)
    if after is not None:
        after_end, after_id = after
        query = query.where(
            or_(
                ObservedTrip.end_time < after_end,
                and_(ObservedTrip.end_time == after_end, ObservedTrip.id > after_id),
            )
        )
    query = query.order_by(ObservedTrip.end_time.desc(), ObservedTrip.id).limit(limit)
    return [_to_leg(row, now=now) for row in db.execute(query).all()]


def count_legs(db: Session, rng: LegRange) -> int:
    """How many rows ``list_legs`` would return."""

    query = select(func.count()).select_from(ObservedTrip).where(*_leg_filters(rng))
    return int(db.scalar(query) or 0)


def count_simulated_legs(db: Session, rng: LegRange) -> int:
    """Non-rejected simulated legs in the range (D11 ``excluded_simulated_trips``)."""

    query = (
        select(func.count())
        .select_from(ObservedTrip)
        .where(
            ObservedTrip.user_id == rng.user_id,
            ObservedTrip.vehicle_id == rng.vehicle_id,
            ObservedTrip.end_time >= rng.start,
            ObservedTrip.end_time < rng.end,
            ObservedTrip.review_status != "rejected",
            ObservedTrip.is_simulated.is_(True),
        )
    )
    return int(db.scalar(query) or 0)


def list_plan_scores(
    db: Session, rng: LegRange, *, model_version: str = DETERMINISTIC_MODEL_VERSION
) -> list[PlanScore]:
    """Plan scores whose chain's last (non-rejected) leg ended in the range."""

    chain_end = (
        select(
            ObservedTrip.planned_trip_id.label("planned_trip_id"),
            func.max(ObservedTrip.end_time).label("chain_end"),
        )
        .where(
            ObservedTrip.user_id == rng.user_id,
            ObservedTrip.vehicle_id == rng.vehicle_id,
            ObservedTrip.planned_trip_id.is_not(None),
            ObservedTrip.review_status != "rejected",
        )
        .group_by(ObservedTrip.planned_trip_id)
        .subquery()
    )
    query = (
        select(
            PredictionResult.planned_trip_id,
            PredictionResult.predicted_energy_kwh,
            PredictionResult.observed_energy_kwh,
            PredictionResult.error_kwh,
            PredictionResult.arrival_soc_error_points,
        )
        .join(chain_end, chain_end.c.planned_trip_id == PredictionResult.planned_trip_id)
        .where(
            PredictionResult.user_id == rng.user_id,
            PredictionResult.vehicle_id == rng.vehicle_id,
            PredictionResult.kind == "plan",
            PredictionResult.model_version == model_version,
            chain_end.c.chain_end >= rng.start,
            chain_end.c.chain_end < rng.end,
        )
        .order_by(chain_end.c.chain_end, PredictionResult.planned_trip_id)
    )
    if not rng.include_simulated:
        query = query.where(PredictionResult.is_simulated.is_(False))
    return [
        PlanScore(
            planned_trip_id=row.planned_trip_id,
            predicted_energy_kwh=float(row.predicted_energy_kwh),
            observed_energy_kwh=_float(row.observed_energy_kwh),
            error_kwh=_float(row.error_kwh),
            arrival_soc_error_points=_float(row.arrival_soc_error_points),
        )
        for row in db.execute(query).all()
    ]


# --------------------------------------------------------------------------- internals


def _leg_filters(rng: LegRange) -> list[Any]:
    filters: list[Any] = [
        ObservedTrip.user_id == rng.user_id,
        ObservedTrip.vehicle_id == rng.vehicle_id,
        ObservedTrip.end_time >= rng.start,
        ObservedTrip.end_time < rng.end,
        ObservedTrip.review_status != "rejected",
    ]
    if not rng.include_simulated:
        filters.append(ObservedTrip.is_simulated.is_(False))
    return filters


def _legs_query(rng: LegRange, model_version: str) -> Select[Any]:
    return (
        select(
            ObservedTrip,
            PredictionResult.predicted_energy_kwh,
            PlannedTrip.origin,
            PlannedTrip.destination,
        )
        .outerjoin(
            PredictionResult,
            and_(
                PredictionResult.observed_trip_id == ObservedTrip.id,
                PredictionResult.kind == "leg",
                PredictionResult.model_version == model_version,
            ),
        )
        .outerjoin(
            PlannedTrip,
            and_(
                PlannedTrip.id == ObservedTrip.planned_trip_id,
                PlannedTrip.user_id == ObservedTrip.user_id,
            ),
        )
        .where(*_leg_filters(rng))
    )


def _to_leg(row: Any, *, now: datetime) -> AnalyticsLeg:
    leg: ObservedTrip = row[0]
    estimated = _float(row[1])
    energy = _float(leg.energy_kwh)
    end_time = _utc(leg.end_time)
    error = None if energy is None or estimated is None else round(energy - estimated, 3)
    return AnalyticsLeg(
        id=leg.id,
        vehicle_id=leg.vehicle_id,
        start_time=_utc(leg.start_time),
        start_time_latest=_utc(leg.start_time_latest),
        end_time=end_time,
        distance_km=float(leg.distance_km),
        soc_start_percent=_float(leg.soc_start_percent),
        soc_end_percent=_float(leg.soc_end_percent),
        energy_kwh=energy,
        energy_kwh_uncertainty=_float(leg.energy_kwh_uncertainty),
        energy_source=leg.energy_source,
        quality_flag=leg.quality_flag,
        quality_reasons=tuple(str(r) for r in (leg.quality_reasons or [])),
        review_status=leg.review_status,
        in_progress=not leg.closed and now - end_time < IN_PROGRESS_WINDOW,
        planned_trip_id=leg.planned_trip_id,
        plan_origin_label=_label(row[2]),
        plan_destination_label=_label(row[3]),
        is_simulated=leg.is_simulated,
        estimated_energy_kwh=estimated,
        error_kwh=error,
    )


def _label(endpoint: Any) -> str | None:
    if not isinstance(endpoint, dict):
        return None
    label = endpoint.get("label")
    return label if isinstance(label, str) else None


def _float(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


__all__: Sequence[str] = [
    "DETERMINISTIC_MODEL_VERSION",
    "IN_PROGRESS_WINDOW",
    "AnalyticsLeg",
    "LegRange",
    "PlanScore",
    "count_legs",
    "count_simulated_legs",
    "list_legs",
    "list_plan_scores",
    "page_legs",
]
