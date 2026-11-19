"""HTTP routes for observed trips (``docs/api-contracts.md`` §3.18-3.18d, Phase 5 F3).

The routes live under ``/observed-trips``, not ``/trips/observed``: the planned
trips router (registered first) has ``GET /trips/{id}``, which would swallow
``/trips/observed``. A leg or vehicle the caller doesn't own is ``NOT_FOUND``.

Importing this module registers trip inference as a telemetry post-ingest hook
(ADR 0009); ``main.py`` imports it with the other routers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.trips import observed_service as service
from app.modules.trips.observed_service import (
    ObservedTripList,
    ObservedTripOut,
    RecomputeRequest,
    RecomputeResponse,
    ReviewStatus,
)
from app.modules.vehicles.ingest_hooks import register_ingest_hook

register_ingest_hook(service.on_telemetry_ingested)

router = APIRouter(prefix="/observed-trips", tags=["observed-trips"])


@router.get("", response_model=ObservedTripList)
def list_observed_trips(
    vehicle_id: uuid.UUID | None = None,
    review_status: list[ReviewStatus] | None = Query(
        default=None, description="Repeatable. Default: every status except rejected."
    ),
    from_time: datetime | None = Query(default=None, alias="from"),
    to_time: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
    cursor: str | None = Query(default=None, max_length=512),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ObservedTripList:
    """The caller's driving legs, newest first."""

    page = service.list_observed_trips(
        db,
        user_id=current_user.user_id,
        vehicle_id=vehicle_id,
        review_statuses=review_status,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        cursor=cursor,
        settings=service.inference_settings(settings),
    )
    return ObservedTripList(items=page.items, next_cursor=page.next_cursor)


@router.post("/recompute", response_model=RecomputeResponse)
def recompute(
    body: RecomputeRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RecomputeResponse:
    """Re-run inference and plan matching for one vehicle, synchronously (catch-up)."""

    service.ensure_vehicle_owned(db, user_id=current_user.user_id, vehicle_id=body.vehicle_id)
    summary = service.run_for_vehicle(
        db,
        vehicle_id=body.vehicle_id,
        user_id=current_user.user_id,
        full=body.full,
        settings=service.inference_settings(settings),
    )
    db.commit()
    return summary.response()


@router.get("/{trip_id}", response_model=ObservedTripOut)
def get_observed_trip(
    trip_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ObservedTripOut:
    """One leg, rejected ones included."""

    return service.get_observed_trip(
        db,
        user_id=current_user.user_id,
        trip_id=trip_id,
        settings=service.inference_settings(settings),
    )


@router.post("/{trip_id}/confirm", response_model=ObservedTripOut)
def confirm(
    trip_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ObservedTripOut:
    """Mark a leg as a real trip (frozen). Idempotent."""

    out = service.confirm(db, user_id=current_user.user_id, trip_id=trip_id)
    db.commit()
    return out


@router.post("/{trip_id}/reject", response_model=ObservedTripOut)
def reject(
    trip_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ObservedTripOut:
    """Mark a leg as not the caller's trip (frozen, un-matched). Idempotent."""

    out = service.reject(db, user_id=current_user.user_id, trip_id=trip_id)
    db.commit()
    return out


__all__ = ["router"]
