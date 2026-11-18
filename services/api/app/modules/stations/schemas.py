"""Request/response models for the stations module.

``docs/api-contracts.md`` §3.10 (``GET /stations``), §3.16 (``GET /stations/{id}``)
and §3.17 (``POST /stations/along-route``). Provider fields the provider doesn't
give stay ``null``; availability, power, counts and pricing are never guessed
(PRD §7).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.integrations.stations.base import (
    ChargingLevel,
    ConnectorType,
    StationAccess,
    StationConnector,
    StationStatus,
)
from app.schemas.geo import Coordinates

# A Frederick → Toronto polyline6 is about 43 KB (phase3_plan.md D6).
MAX_GEOMETRY_BYTES = 200_000
# Along-route results are sorted along the whole route, so they aren't paged;
# this caps how many the server collects (5 AFDC pages of 200).
MAX_ALONG_ROUTE_RESULTS = 1000
MIN_CORRIDOR_KM = 0.5
MAX_CORRIDOR_KM = 16.0
DEFAULT_CORRIDOR_KM = 3.0
MAX_RADIUS_KM = 80.0


class StationOut(BaseModel):
    """One charging station from the ``charging_stations`` cache."""

    id: uuid.UUID
    provider: str
    external_id: str
    name: str
    operator: str | None
    network: str | None
    street_address: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    country: str | None
    location: Coordinates
    connectors: list[StationConnector]
    # Highest known connector power; null if no connector's power is known (D10).
    max_power_kw: float | None
    access: StationAccess
    access_detail: str | None
    # Public but limited (e.g. dealer-only). Searches leave these out.
    restricted_access: bool
    status: StationStatus
    pricing_text: str | None
    # True if any connector matches the vehicle's; null when no vehicle (or its
    # connectors are unknown).
    compatible: bool | None
    # Never fabricated: no provider gives verified live data in Phase 3.
    live_availability: None = None
    source_updated_at: datetime | None
    # When Chargewise last fetched this station (the ``recorded_at`` of §1).
    fetched_at: datetime
    is_stale: bool


class StationListResponse(BaseModel):
    items: list[StationOut]
    next_cursor: str | None
    # Provider's total match count before the power filter (and incl. restricted).
    total_results: int
    # Items on this page whose power is unknown (kept by the power filter, D10).
    unknown_power_count: int


class AlongRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geometry_polyline6: str = Field(min_length=1, max_length=MAX_GEOMETRY_BYTES)
    corridor_km: float = Field(
        default=DEFAULT_CORRIDOR_KM, ge=MIN_CORRIDOR_KM, le=MAX_CORRIDOR_KM, allow_inf_nan=False
    )
    connector: list[ConnectorType] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list, max_length=20)
    # D6: DC fast by default (96 vs 955 results on the test route); "all" for every level.
    charging_level: ChargingLevel | Literal["all"] = "dc_fast"
    min_power_kw: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    vehicle_id: uuid.UUID | None = None
    limit: int = Field(default=200, ge=1, le=MAX_ALONG_ROUTE_RESULTS)


class RouteStationOut(StationOut):
    distance_along_route_km: float
    distance_from_route_km: float


class AlongRouteResponse(BaseModel):
    items: list[RouteStationOut]
    total_results: int
    unknown_power_count: int
    # True when the provider had more matches than ``limit`` collected.
    truncated: bool


__all__ = [
    "DEFAULT_CORRIDOR_KM",
    "MAX_ALONG_ROUTE_RESULTS",
    "MAX_CORRIDOR_KM",
    "MAX_GEOMETRY_BYTES",
    "MAX_RADIUS_KM",
    "MIN_CORRIDOR_KM",
    "AlongRouteRequest",
    "AlongRouteResponse",
    "RouteStationOut",
    "StationListResponse",
    "StationOut",
]
