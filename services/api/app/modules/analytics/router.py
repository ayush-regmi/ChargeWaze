"""HTTP routes for analytics (``docs/api-contracts.md`` §3.11, §3.11a, §3.11b).

All three share the filters ``vehicle_id``, ``from``, ``to``, ``tz`` and
``include_simulated``. Another user's vehicle is ``NOT_FOUND``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.modules.analytics import service
from app.modules.analytics.buckets import GroupBy
from app.modules.analytics.schemas import AnalyticsTripList, ConsumptionResponse
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _filters(
    vehicle_id: uuid.UUID,
    from_: date | None = Query(
        default=None, alias="from", description="First local date (inclusive)."
    ),
    to: date | None = Query(default=None, description="Last local date (inclusive)."),
    tz: str = Query(default="UTC", max_length=64, description="IANA time zone."),
    include_simulated: bool = False,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> service.Filters:
    return service.resolve_filters(
        db,
        user_id=current_user.user_id,
        vehicle_id=vehicle_id,
        first=from_,
        last=to,
        tz=tz,
        include_simulated=include_simulated,
        settings=settings,
        now=datetime.now(UTC),
    )


@router.get("/consumption", response_model=ConsumptionResponse)
def get_consumption(
    group_by: GroupBy = "week",
    filters: service.Filters = Depends(_filters),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ConsumptionResponse:
    """Consumption and model error per bucket for one vehicle's observed legs."""

    return service.consumption(
        db,
        user_id=current_user.user_id,
        filters=filters,
        group_by=group_by,
        settings=settings,
        now=datetime.now(UTC),
    )


@router.get("/trips", response_model=AnalyticsTripList)
def list_analytics_trips(
    limit: int = Query(default=service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
    cursor: str | None = Query(default=None, max_length=512),
    filters: service.Filters = Depends(_filters),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalyticsTripList:
    """The analytics table: one row per non-rejected leg, newest first."""

    return service.trips_page(
        db,
        user_id=current_user.user_id,
        filters=filters,
        limit=limit,
        cursor=cursor,
        now=datetime.now(UTC),
    )


@router.get(
    "/trips.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_analytics_trips_csv(
    filters: service.Filters = Depends(_filters),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Every row of ``/analytics/trips`` for the same filters, as CSV."""

    export = service.trips_csv(
        db,
        user_id=current_user.user_id,
        filters=filters,
        settings=settings,
        now=datetime.now(UTC),
    )
    return Response(
        content=export.body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )


__all__ = ["router"]
