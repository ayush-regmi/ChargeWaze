"""HTTP routes for place search (``docs/api-contracts.md`` §3.13-3.14).

A thin proxy over the Mapbox adapter (phase3_plan.md D2): the browser talks only
to our API, and the server token never leaves the server. Nothing is stored,
and this module logs nothing itself; the adapter's request log carries no
search text, IDs or coordinates (D13).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import AfterValidator

from app.core.errors import ApiError
from app.integrations.mapbox.client import (
    SUGGEST_LIMIT_MAX,
    MapboxClient,
    get_mapbox_client,
)
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.auth.rate_limit import rate_limit
from app.modules.places.schemas import (
    PLACE_ID_MAX_LENGTH,
    QUERY_MAX_LENGTH,
    QUERY_MIN_LENGTH,
    SUGGEST_LIMIT_DEFAULT,
    PlaceOut,
    PlaceSuggestionList,
    PlaceSuggestionOut,
)
from app.schemas.geo import Coordinates

router = APIRouter(prefix="/places", tags=["places"])


def _trimmed_query(value: str) -> str:
    trimmed = value.strip()
    if not QUERY_MIN_LENGTH <= len(trimmed) <= QUERY_MAX_LENGTH:
        raise ValueError(
            f"q must be {QUERY_MIN_LENGTH}-{QUERY_MAX_LENGTH} characters after trimming."
        )
    return trimmed


SessionToken = Annotated[
    uuid.UUID,
    Query(description="Client-generated UUID, one per search session (Mapbox billing)."),
]


@router.get(
    "/suggest",
    response_model=PlaceSuggestionList,
    dependencies=[Depends(rate_limit("suggest"))],
)
def suggest_places(
    q: Annotated[str, Query(description="Search text."), AfterValidator(_trimmed_query)],
    session_token: SessionToken,
    lat: Annotated[float | None, Query(ge=-90, le=90, allow_inf_nan=False)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180, allow_inf_nan=False)] = None,
    limit: Annotated[int, Query(ge=1, le=SUGGEST_LIMIT_MAX)] = SUGGEST_LIMIT_DEFAULT,
    _user: AuthenticatedUser = Depends(get_current_user),
    mapbox: MapboxClient = Depends(get_mapbox_client),
) -> PlaceSuggestionList:
    """Autocomplete addresses, places and POIs. Results have no coordinates."""

    if (lat is None) != (lon is None):
        raise _validation_error("lat and lon must be given together.", ["query", "lat"])
    proximity = (lat, lon) if lat is not None and lon is not None else None

    suggestions = mapbox.suggest(
        q, session_token=str(session_token), proximity=proximity, limit=limit
    )
    return PlaceSuggestionList(
        items=[
            PlaceSuggestionOut(
                id=s.id,
                name=s.name,
                full_address=s.full_address,
                kind=s.kind,
                country_code=s.country_code,
            )
            for s in suggestions
        ]
    )


@router.get("/{place_id}", response_model=PlaceOut)
def get_place(
    place_id: Annotated[str, Path(min_length=1, max_length=PLACE_ID_MAX_LENGTH)],
    session_token: SessionToken,
    _user: AuthenticatedUser = Depends(get_current_user),
    mapbox: MapboxClient = Depends(get_mapbox_client),
) -> PlaceOut:
    """Coordinates and address for a suggestion ID from ``/places/suggest``."""

    place = mapbox.retrieve(place_id, session_token=str(session_token))
    if place is None:
        raise ApiError("NOT_FOUND", "Place not found.", status_code=status.HTTP_404_NOT_FOUND)
    return PlaceOut(
        id=place.id,
        name=place.name,
        full_address=place.full_address,
        kind=place.kind,
        country_code=place.country_code,
        location=Coordinates(lat=place.lat, lon=place.lon),
    )


def _validation_error(message: str, loc: list[str]) -> ApiError:
    """Same envelope as FastAPI's own validation errors (``VALIDATION_ERROR`` 422)."""

    return ApiError(
        "VALIDATION_ERROR",
        "Request failed validation.",
        status_code=422,
        details={"errors": [{"loc": loc, "msg": message, "type": "value_error"}]},
    )
