"""Mapbox HTTP client: the only code in Chargewise that talks to Mapbox.

Covers Directions (phase3_plan.md D5) and Search Box suggest/retrieve (D2).
Facts behind the request shapes are in docs/maps-stations-notes.md §2.

Logging rule (D13): one line per request with method, endpoint *template*,
status and duration. Never log the access token, session tokens, search text,
Mapbox IDs, or coordinates. Mapbox only accepts the token in the query string,
so request URLs are never logged (httpx's own request log is silenced in
``app.main``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from urllib.parse import quote

import httpx
from fastapi import status
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.core.geo import LatLon
from app.integrations.mapbox import errors
from app.integrations.mapbox.schemas import (
    DirectionsResult,
    DirectionsRoute,
    NoRouteReason,
    Place,
    PlaceSuggestion,
    UpstreamDirections,
    UpstreamRetrieveResponse,
    UpstreamSuggestResponse,
    country_code,
    display_address,
    place_kind,
)

logger = logging.getLogger(__name__)

PROVIDER = "mapbox"
DIRECTIONS_PROFILE = "mapbox/driving"
SUGGEST_LIMIT_MAX = 10

# D5: alternatives on, full polyline6 overview, no turn-by-turn steps, no traffic.
DIRECTIONS_PARAMS: Mapping[str, str] = {
    "alternatives": "true",
    "geometries": "polyline6",
    "overview": "full",
    "steps": "false",
}

# Mapbox answers HTTP 200 with these codes and ``routes: []`` (notes §2.1).
_NO_ROUTE_CODES: Mapping[str, NoRouteReason] = {
    "NoRoute": "no_route",
    "NoSegment": "no_road_near_point",
}


class MapboxClient:
    """Thin, typed wrapper over Mapbox Directions and Search Box.

    Stateless apart from the ``httpx.Client``, so one instance is shared across
    FastAPI's threadpool. Pass ``transport`` (e.g. ``httpx.MockTransport``) in
    tests; production uses the default network transport.
    """

    def __init__(
        self,
        *,
        access_token: str,
        api_base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._access_token = access_token
        self._api_base_url = api_base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.BaseTransport | None = None
    ) -> MapboxClient:
        return cls(
            access_token=settings.mapbox_secret_token,
            api_base_url=settings.mapbox_api_base_url,
            timeout_seconds=settings.mapbox_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Directions
    # ------------------------------------------------------------------

    def directions(self, origin: LatLon, destination: LatLon) -> DirectionsResult:
        """Driving routes from ``origin`` to ``destination``, best first (up to 3).

        "No route" and "no road near a point" are results, not errors: they come
        back as an empty ``routes`` list with ``no_route_reason`` set.
        """

        coordinates = f"{_lon_lat(origin)};{_lon_lat(destination)}"
        response = self._get(
            f"/directions/v5/{DIRECTIONS_PROFILE}/{coordinates}",
            endpoint=f"directions/v5/{DIRECTIONS_PROFILE}",
            params=DIRECTIONS_PARAMS,
        )
        body = _parse(UpstreamDirections, response)

        reason = _NO_ROUTE_CODES.get(body.code)
        if reason is not None:
            return DirectionsResult(routes=[], no_route_reason=reason)
        if body.code != "Ok":
            raise errors.unexpected_response(body.code)
        if not body.routes:
            # "Ok" with nothing in it isn't documented; treat it as no route.
            return DirectionsResult(routes=[], no_route_reason="no_route")

        return DirectionsResult(
            routes=[
                DirectionsRoute(
                    geometry_polyline6=route.geometry,
                    distance_m=route.distance,
                    duration_s=route.duration,
                    summary=", ".join(leg.summary for leg in route.legs if leg.summary) or None,
                    countries=_countries(
                        admin.iso_3166_1 for leg in route.legs for admin in leg.admins
                    ),
                )
                for route in body.routes
            ]
        )

    # ------------------------------------------------------------------
    # Search Box
    # ------------------------------------------------------------------

    def suggest(
        self,
        query: str,
        *,
        session_token: str,
        proximity: LatLon | None = None,
        countries: Sequence[str] | None = None,
        limit: int = 5,
        language: str = "en",
    ) -> list[PlaceSuggestion]:
        """Autocomplete addresses, places and POIs. Results carry no coordinates.

        ``proximity`` should always be given (the map centre or the other trip
        endpoint). Without it Mapbox biases by the caller's IP, which is our
        server's, so we fall back to ``ip`` only as a last resort (D2).
        """

        if not 1 <= limit <= SUGGEST_LIMIT_MAX:
            raise ValueError(f"limit must be between 1 and {SUGGEST_LIMIT_MAX}.")
        params: dict[str, str] = {
            "q": query,
            "session_token": session_token,
            "proximity": _lon_lat(proximity) if proximity is not None else "ip",
            "limit": str(limit),
            "language": language,
        }
        if countries:
            params["country"] = ",".join(c.lower() for c in countries)

        response = self._get(
            "/search/searchbox/v1/suggest",
            endpoint="search/searchbox/v1/suggest",
            params=params,
        )
        body = _parse(UpstreamSuggestResponse, response)
        return [
            PlaceSuggestion(
                id=s.mapbox_id,
                name=s.name,
                full_address=display_address(s.name, s.full_address, s.place_formatted),
                kind=place_kind(s.feature_type),
                country_code=country_code(s.context),
            )
            for s in body.suggestions
        ]

    def retrieve(self, mapbox_id: str, *, session_token: str) -> Place | None:
        """Coordinates and address for a suggestion. ``None`` if Mapbox doesn't know the ID."""

        response = self._get(
            f"/search/searchbox/v1/retrieve/{quote(mapbox_id, safe='')}",
            endpoint="search/searchbox/v1/retrieve/{id}",
            params={"session_token": session_token},
            not_found_ok=True,
        )
        if response.status_code == status.HTTP_404_NOT_FOUND:
            return None
        body = _parse(UpstreamRetrieveResponse, response)
        if not body.features:
            return None
        props = body.features[0].properties
        return Place(
            id=props.mapbox_id,
            name=props.name,
            full_address=display_address(props.name, props.full_address, props.place_formatted),
            kind=place_kind(props.feature_type),
            lat=props.coordinates.latitude,
            lon=props.coordinates.longitude,
            country_code=country_code(props.context),
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _get(
        self,
        path: str,
        *,
        endpoint: str,
        params: Mapping[str, str],
        not_found_ok: bool = False,
    ) -> httpx.Response:
        if not self._access_token:
            raise errors.not_configured()

        started = time.perf_counter()
        status_code: int | None = None
        try:
            response = self._http.get(
                f"{self._api_base_url}{path}",
                params={**params, "access_token": self._access_token},
            )
            status_code = response.status_code
        except httpx.TransportError as exc:
            raise errors.from_transport_error(exc) from None
        finally:
            logger.info(
                "mapbox_request",
                extra={
                    "provider": PROVIDER,
                    "method": "GET",
                    "endpoint": endpoint,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

        if not_found_ok and response.status_code == status.HTTP_404_NOT_FOUND:
            return response
        if response.is_error:
            raise errors.from_response(response)
        return response


def _lon_lat(point: LatLon) -> str:
    """``lon,lat`` with 6 decimals, the order Mapbox URLs use."""

    lat, lon = point
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordinates out of range.")
    return f"{lon:.6f},{lat:.6f}"


def _countries(codes: Iterable[str | None]) -> list[str]:
    """Upper-case country codes in first-seen order, repeats removed."""

    seen: list[str] = []
    for code in codes:
        if code and code.upper() not in seen:
            seen.append(code.upper())
    return seen


def _parse[ModelT: BaseModel](model: type[ModelT], response: httpx.Response) -> ModelT:
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError):
        # ValidationError is a ValueError subclass; both listed for clarity.
        raise errors.unexpected_response() from None


@lru_cache(maxsize=1)
def _default_client() -> MapboxClient:
    return MapboxClient.from_settings(get_settings())


def get_mapbox_client() -> MapboxClient:
    """FastAPI dependency. Tests override it with a fake or a MockTransport client."""

    return _default_client()


__all__ = ["DIRECTIONS_PARAMS", "MapboxClient", "get_mapbox_client"]
