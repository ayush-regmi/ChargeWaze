"""HTTP routes for the routing module.

- ``POST /routes/directions`` (``docs/api-contracts.md`` §3.15): plain driving
  directions. Nothing is stored.
- ``POST /routes/plan`` (§3.7, Phase 4 F4): the charging-aware plan, stored as an
  unsaved trip (phase4_plan.md D6). Logic lives in ``planner_service``.

Coordinates and geometry are never logged (phase3_plan.md D13).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.integrations.mapbox.client import MapboxClient, get_mapbox_client
from app.integrations.stations import StationProvider, get_station_provider
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.routing import planner_service
from app.modules.routing.schemas import (
    DirectionsRequest,
    DirectionsResponse,
    DirectionsRouteOut,
    PlanRequest,
    PlanResponse,
)

router = APIRouter(prefix="/routes", tags=["routing"])


@router.post("/directions", response_model=DirectionsResponse)
def get_directions(
    body: DirectionsRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    mapbox: MapboxClient = Depends(get_mapbox_client),
) -> DirectionsResponse:
    """Up to three driving routes, best first. "No route" is a 200 with ``routes: []``."""

    result = mapbox.directions(body.origin.as_tuple(), body.destination.as_tuple())
    return DirectionsResponse(
        routes=[
            DirectionsRouteOut(
                index=i,
                geometry_polyline6=route.geometry_polyline6,
                distance_m=route.distance_m,
                duration_s=route.duration_s,
                summary=route.summary,
                countries=route.countries,
            )
            for i, route in enumerate(result.routes)
        ],
        no_route_reason=result.no_route_reason,
    )


@router.post("/plan", response_model=PlanResponse)
def plan_route(
    body: PlanRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mapbox: MapboxClient = Depends(get_mapbox_client),
    provider: StationProvider = Depends(get_station_provider),
) -> PlanResponse:
    """Plan a trip with charging stops and store it unsaved. Infeasible is a 200."""

    result = planner_service.plan_trip(
        db,
        user_id=user.user_id,
        request=body,
        settings=settings,
        mapbox=mapbox,
        station_provider=provider,
    )
    db.commit()
    return result
