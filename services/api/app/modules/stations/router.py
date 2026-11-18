"""HTTP routes for the stations module (``docs/api-contracts.md`` §3.10, §3.16, §3.17).

All three endpoints require sign-in. Coordinates, routes and search filters are
never logged (phase3_plan.md D13); the service logs counts only.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.geo import decode_polyline
from app.db.session import get_db
from app.integrations.stations import StationFilters, StationProvider, get_station_provider
from app.integrations.stations.base import ChargingLevel, ConnectorType
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.stations import service
from app.modules.stations.schemas import (
    MAX_RADIUS_KM,
    AlongRouteRequest,
    AlongRouteResponse,
    StationListResponse,
    StationOut,
)

router = APIRouter(prefix="/stations", tags=["stations"])


@router.get("", response_model=StationListResponse)
def list_stations(
    lat: float = Query(ge=-90, le=90, allow_inf_nan=False),
    lon: float = Query(ge=-180, le=180, allow_inf_nan=False),
    radius_km: float = Query(default=25.0, gt=0, le=MAX_RADIUS_KM, allow_inf_nan=False),
    connector: list[ConnectorType] | None = Query(default=None),
    network: list[str] | None = Query(default=None, max_length=20),
    charging_level: ChargingLevel | None = None,
    min_power_kw: float | None = Query(default=None, ge=0, le=1000, allow_inf_nan=False),
    vehicle_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=512),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: StationProvider = Depends(get_station_provider),
    settings: Settings = Depends(get_settings),
) -> StationListResponse:
    """Stations near a point, nearest first. Defaults to the vehicle's connectors."""

    digest = service.query_digest(
        {
            "user": user.user_id,
            "lat": lat,
            "lon": lon,
            "radius_km": radius_km,
            "connector": sorted(connector or []),
            "network": sorted(network or []),
            "charging_level": charging_level,
            "min_power_kw": min_power_kw,
            "vehicle_id": vehicle_id,
            "limit": limit,
        }
    )
    secret = settings.stations_cursor_secret
    offset = 0 if cursor is None else service.decode_cursor(cursor, digest=digest, secret=secret)

    vehicle = _vehicle_connectors(db, user, vehicle_id)
    filters = StationFilters(
        connector_types=_effective_connectors(connector, vehicle),
        networks=tuple(network or ()),
        charging_level=charging_level,
        limit=limit,
        offset=offset,
    )
    result = service.search_nearby(
        db,
        provider,
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        filters=filters,
        min_power_kw=min_power_kw,
        vehicle_connectors=vehicle,
    )
    db.commit()
    next_cursor = (
        None
        if result.next_offset is None
        else service.encode_cursor(result.next_offset, digest=digest, secret=secret)
    )
    return StationListResponse(
        items=result.items,
        next_cursor=next_cursor,
        total_results=result.total_results,
        unknown_power_count=result.unknown_power_count,
    )


@router.post("/along-route", response_model=AlongRouteResponse)
def stations_along_route(
    body: AlongRouteRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: StationProvider = Depends(get_station_provider),
    settings: Settings = Depends(get_settings),
) -> AlongRouteResponse:
    """Stations within ``corridor_km`` of a route, in order along the route."""

    route = _decode_route(body.geometry_polyline6)
    vehicle = _vehicle_connectors(db, user, body.vehicle_id)
    filters = StationFilters(
        connector_types=_effective_connectors(body.connector or None, vehicle),
        networks=tuple(body.network),
        charging_level=None if body.charging_level == "all" else body.charging_level,
    )
    result = service.search_along_route(
        db,
        provider,
        route=route,
        corridor_km=body.corridor_km,
        filters=filters,
        min_power_kw=body.min_power_kw,
        vehicle_connectors=vehicle,
        max_results=body.limit,
        route_max_points=settings.stations_route_max_points,
    )
    db.commit()
    return result


@router.get("/{station_id}", response_model=StationOut)
def get_station(
    station_id: uuid.UUID,
    vehicle_id: uuid.UUID | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: StationProvider = Depends(get_station_provider),
    settings: Settings = Depends(get_settings),
) -> StationOut:
    """One cached station; refreshed from the provider after the cache TTL."""

    vehicle = _vehicle_connectors(db, user, vehicle_id)
    station = service.get_station(
        db,
        provider,
        station_id,
        ttl=timedelta(hours=settings.stations_cache_ttl_hours),
        vehicle_connectors=vehicle,
    )
    db.commit()
    return station


def _vehicle_connectors(
    db: Session, user: AuthenticatedUser, vehicle_id: uuid.UUID | None
) -> frozenset[ConnectorType] | None:
    if vehicle_id is None:
        return None
    return service.connectors_for_vehicle(db, user_id=user.user_id, vehicle_id=vehicle_id)


def _effective_connectors(
    explicit: list[ConnectorType] | None, vehicle: frozenset[ConnectorType] | None
) -> tuple[ConnectorType, ...]:
    """An explicit ``connector`` filter wins; otherwise the vehicle's connectors."""

    if explicit:
        return tuple(dict.fromkeys(explicit))
    if vehicle:
        return tuple(sorted(vehicle))
    return ()


def _decode_route(encoded: str) -> list[tuple[float, float]]:
    try:
        points = decode_polyline(encoded)
    # OverflowError: a long run of continuation bytes decodes to an integer too
    # large for a float. It is a malformed polyline, not a server error.
    except (ValueError, OverflowError):
        raise _invalid_geometry("geometry_polyline6 is not a valid polyline6 string.") from None
    if len(points) < 2:
        raise _invalid_geometry("geometry_polyline6 needs at least two points.")
    if any(not (-90 <= lat <= 90 and -180 <= lon <= 180) for lat, lon in points):
        raise _invalid_geometry("geometry_polyline6 has coordinates out of range.")
    return points


def _invalid_geometry(message: str) -> ApiError:
    return ApiError("VALIDATION_ERROR", message, status_code=422)
