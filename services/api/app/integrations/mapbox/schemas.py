"""Mapbox response models.

Two layers: ``_Upstream*`` models parse the raw Mapbox JSON (ignoring fields we
don't use), and the public models are what the adapter returns. Nothing
outside this package should see Mapbox's raw shapes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlaceKind = Literal["address", "poi", "place"]
NoRouteReason = Literal["no_route", "no_road_near_point"]

# Search Box ``feature_type`` → our coarse kind. Anything not listed (``place``,
# ``locality``, ``neighborhood``, ``region``, ``postcode``, ``country``, …) is a
# ``place``.
_KIND_BY_FEATURE_TYPE: dict[str, PlaceKind] = {
    "address": "address",
    "street": "address",
    "poi": "poi",
}


def place_kind(feature_type: str) -> PlaceKind:
    return _KIND_BY_FEATURE_TYPE.get(feature_type, "place")


# ---------------------------------------------------------------------------
# Public (adapter output)
# ---------------------------------------------------------------------------


class DirectionsRoute(BaseModel):
    """One driving route. Geometry is Mapbox ``polyline6`` (``lat, lon``, 1e-6)."""

    geometry_polyline6: str
    distance_m: float
    duration_s: float
    # Mapbox's road summary, e.g. "US 15, QEW". Empty when Mapbox gives none.
    summary: str | None = None
    # ISO 3166-1 alpha-2 codes of the countries crossed, in route order, no repeats.
    countries: list[str] = Field(default_factory=list)


class DirectionsResult(BaseModel):
    """Routes, best first. ``routes`` is empty iff ``no_route_reason`` is set."""

    routes: list[DirectionsRoute]
    no_route_reason: NoRouteReason | None = None


class PlaceSuggestion(BaseModel):
    """A Search Box suggestion. Has no coordinates: call ``retrieve`` for those."""

    id: str
    name: str
    # Display line: Mapbox's ``full_address`` for addresses and POIs, otherwise
    # ``name, place_formatted`` (e.g. "Toronto, Ontario, Canada").
    full_address: str
    kind: PlaceKind
    country_code: str | None = None


class Place(BaseModel):
    id: str
    name: str
    full_address: str
    kind: PlaceKind
    lat: float
    lon: float
    country_code: str | None = None


# ---------------------------------------------------------------------------
# Upstream (raw Mapbox JSON)
# ---------------------------------------------------------------------------


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _UpstreamAdmin(_Lenient):
    iso_3166_1: str | None = None


class _UpstreamLeg(_Lenient):
    summary: str | None = None
    admins: list[_UpstreamAdmin] = Field(default_factory=list)


class _UpstreamRoute(_Lenient):
    geometry: str
    distance: float
    duration: float
    legs: list[_UpstreamLeg] = Field(default_factory=list)


class UpstreamDirections(_Lenient):
    code: str
    # Absent on some failure codes; required (may be empty) when ``code`` is ``Ok``.
    routes: list[_UpstreamRoute] | None = None


class _UpstreamCountry(_Lenient):
    country_code: str | None = None


class _UpstreamContext(_Lenient):
    country: _UpstreamCountry | None = None


class UpstreamSuggestion(_Lenient):
    mapbox_id: str
    name: str
    feature_type: str
    full_address: str | None = None
    place_formatted: str | None = None
    context: _UpstreamContext | None = None


class UpstreamSuggestResponse(_Lenient):
    # Required: a body without it is malformed, not "no matches".
    suggestions: list[UpstreamSuggestion]


class _UpstreamCoordinates(_Lenient):
    latitude: float
    longitude: float


class _UpstreamFeatureProperties(_Lenient):
    mapbox_id: str
    name: str
    feature_type: str
    full_address: str | None = None
    place_formatted: str | None = None
    coordinates: _UpstreamCoordinates
    context: _UpstreamContext | None = None


class _UpstreamFeature(_Lenient):
    properties: _UpstreamFeatureProperties


class UpstreamRetrieveResponse(_Lenient):
    # Required: a body without it is malformed, not "unknown ID".
    features: list[_UpstreamFeature]


def display_address(name: str, full_address: str | None, place_formatted: str | None) -> str:
    if full_address:
        return full_address
    if place_formatted:
        return f"{name}, {place_formatted}"
    return name


def country_code(context: _UpstreamContext | None) -> str | None:
    if context and context.country and context.country.country_code:
        return context.country.country_code.upper()
    return None


__all__ = [
    "DirectionsResult",
    "DirectionsRoute",
    "NoRouteReason",
    "Place",
    "PlaceKind",
    "PlaceSuggestion",
    "UpstreamDirections",
    "UpstreamRetrieveResponse",
    "UpstreamSuggestResponse",
    "UpstreamSuggestion",
    "country_code",
    "display_address",
    "place_kind",
]
