"""Request/response models for the places module (``docs/api-contracts.md`` §3.13-3.14)."""

from __future__ import annotations

from pydantic import BaseModel

from app.integrations.mapbox.schemas import PlaceKind
from app.schemas.geo import Coordinates

QUERY_MIN_LENGTH = 3
QUERY_MAX_LENGTH = 200
PLACE_ID_MAX_LENGTH = 512
SUGGEST_LIMIT_DEFAULT = 5


class PlaceSuggestionOut(BaseModel):
    id: str
    name: str
    full_address: str
    kind: PlaceKind
    country_code: str | None


class PlaceSuggestionList(BaseModel):
    items: list[PlaceSuggestionOut]


class PlaceOut(BaseModel):
    id: str
    name: str
    full_address: str
    kind: PlaceKind
    country_code: str | None
    location: Coordinates
