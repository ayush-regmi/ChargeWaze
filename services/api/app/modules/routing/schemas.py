"""Request/response models for the routing module (``docs/api-contracts.md`` §3.15)."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.geo import haversine_km
from app.integrations.mapbox.schemas import NoRouteReason
from app.schemas.geo import Coordinates

MIN_ENDPOINT_SEPARATION_KM = 0.05


class DirectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Coordinates
    destination: Coordinates

    @model_validator(mode="after")
    def _endpoints_apart(self) -> Self:
        separation = haversine_km(self.origin.as_tuple(), self.destination.as_tuple())
        if separation <= MIN_ENDPOINT_SEPARATION_KM:
            raise ValueError("origin and destination must be more than 50 m apart.")
        return self


class DirectionsRouteOut(BaseModel):
    index: int
    geometry_polyline6: str
    distance_m: float
    duration_s: float
    summary: str | None
    countries: list[str]


class DirectionsResponse(BaseModel):
    routes: list[DirectionsRouteOut]
    no_route_reason: NoRouteReason | None
