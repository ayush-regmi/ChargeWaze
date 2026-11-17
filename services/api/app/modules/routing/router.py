"""HTTP routes for the routing module.

Phase 3 exposes plain driving directions only (``docs/api-contracts.md`` §3.15).
The charging-aware ``POST /routes/plan`` (§3.7) arrives in Phase 4. Nothing is
stored, and coordinates and geometry are never logged (phase3_plan.md D13).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.integrations.mapbox.client import MapboxClient, get_mapbox_client
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.routing.schemas import (
    DirectionsRequest,
    DirectionsResponse,
    DirectionsRouteOut,
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
