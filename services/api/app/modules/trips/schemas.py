"""Response models for the trips module (``docs/api-contracts.md`` §3.8-3.9b)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

TripStatus = Literal["planned", "completed", "abandoned"]
PlanStatus = Literal["feasible", "infeasible"]


class TripListItem(BaseModel):
    """One trip in ``GET /trips`` (§3.8); also the ``POST /trips/{id}/save`` body."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    origin_label: str | None
    destination_label: str | None
    status: TripStatus
    plan_status: PlanStatus
    # From the stored plan: null when there was no route.
    distance_km: float | None
    # From the stored plan: null when no feasible plan exists.
    total_minutes: int | None
    stop_count: int
    created_at: datetime
    saved_at: datetime | None


class TripList(BaseModel):
    items: list[TripListItem]
    next_cursor: str | None


class TripObservationLeg(BaseModel):
    """One observed leg of a matched plan (§3.18e), in drive order."""

    id: uuid.UUID
    plan_leg_index: int
    start_time: datetime
    end_time: datetime
    distance_km: float
    soc_start_percent: float | None
    soc_end_percent: float | None
    energy_kwh: float | None
    quality_flag: Literal["high", "medium", "low"]
    review_status: Literal["auto", "needs_review", "confirmed", "rejected"]


class TripObservation(BaseModel):
    """What happened when a plan was driven (§3.18e). SoC values are fractions."""

    matched_at: datetime
    legs: list[TripObservationLeg]
    observed_distance_km: float
    planned_distance_km: float
    predicted_energy_kwh: float
    # Null when any leg has no energy.
    observed_energy_kwh: float | None
    error_kwh: float | None
    observed_start_soc: float | None
    predicted_arrival_soc: float | None
    observed_arrival_soc: float | None
    arrival_soc_error_points: float | None
    model_version: str


class TripDetail(BaseModel):
    """``GET /trips/{id}`` (§3.9). ``plan`` is the stored ``/routes/plan`` body, unchanged."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    status: TripStatus
    plan_status: PlanStatus
    created_at: datetime
    saved_at: datetime | None
    plan: dict[str, Any]
    # The legs that carried the plan out (Phase 5, §3.18e); null until matched.
    observation: TripObservation | None = None


__all__ = [
    "PlanStatus",
    "TripDetail",
    "TripList",
    "TripListItem",
    "TripObservation",
    "TripObservationLeg",
    "TripStatus",
]
