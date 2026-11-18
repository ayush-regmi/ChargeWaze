"""HTTP routes for planned trips (``docs/api-contracts.md`` §3.8, §3.9, §3.9a, §3.9b).

Plans are created only by ``POST /routes/plan`` (F4), never from a
client-sent payload (phase4_plan.md D6). These routes list, read, save and
delete them. A trip the caller doesn't own is always ``NOT_FOUND``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.trips import service
from app.modules.trips.schemas import TripDetail, TripList, TripListItem, TripStatus

router = APIRouter(prefix="/trips", tags=["trips"])


@router.get("", response_model=TripList)
def list_trips(
    vehicle_id: uuid.UUID | None = None,
    saved: bool = Query(default=True, description="Saved trips (default) or unsaved plans."),
    status_filter: TripStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
    cursor: str | None = Query(default=None, max_length=512),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TripList:
    """The caller's planned trips: saved by ``saved_at`` desc, unsaved by ``created_at`` desc."""

    page = service.list_trips(
        db,
        user_id=current_user.user_id,
        saved=saved,
        vehicle_id=vehicle_id,
        trip_status=status_filter,
        limit=limit,
        cursor=cursor,
    )
    return TripList(items=page.items, next_cursor=page.next_cursor)


@router.get("/{trip_id}", response_model=TripDetail)
def get_trip(
    trip_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TripDetail:
    """One trip with its stored plan, saved or not. Nothing is recomputed."""

    return service.get_trip(db, user_id=current_user.user_id, trip_id=trip_id)


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_trip(
    trip_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a trip (saved or not) and its stops."""

    service.delete_trip(db, user_id=current_user.user_id, trip_id=trip_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{trip_id}/save", response_model=TripListItem)
def save_trip(
    trip_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TripListItem:
    """Keep a stored plan in trip history. Idempotent: the first ``saved_at`` stays."""

    item = service.save_trip(db, user_id=current_user.user_id, trip_id=trip_id)
    db.commit()
    return item


__all__ = ["router"]
