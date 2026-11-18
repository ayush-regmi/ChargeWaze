"""Shared request/response models for coordinates."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.geo import LatLon

Latitude = Field(ge=-90, le=90, allow_inf_nan=False)
Longitude = Field(ge=-180, le=180, allow_inf_nan=False)


class Coordinates(BaseModel):
    """A WGS84 point in degrees. Range-checked; NaN and infinity are rejected."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Latitude
    lon: float = Longitude

    def as_tuple(self) -> LatLon:
        return (self.lat, self.lon)


__all__ = ["Coordinates", "Latitude", "Longitude"]
