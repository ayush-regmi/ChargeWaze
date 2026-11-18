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


class TripDetail(BaseModel):
    """``GET /trips/{id}`` (§3.9). ``plan`` is the stored ``/routes/plan`` body, unchanged."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    status: TripStatus
    plan_status: PlanStatus
    created_at: datetime
    saved_at: datetime | None
    plan: dict[str, Any]
    # The matched observed trip, from Phase 5. Always null until then.
    observation: dict[str, Any] | None = None


__all__ = ["PlanStatus", "TripDetail", "TripList", "TripListItem", "TripStatus"]
