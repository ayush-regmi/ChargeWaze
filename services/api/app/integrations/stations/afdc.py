"""AFDC (NREL / NLR) Alternative Fuel Stations adapter: the only code that talks to AFDC.

Facts behind every request shape are in docs/maps-stations-notes.md §1 and
the decisions in docs/phase3_plan.md (D1, D6, D8, D9, D10).

- Every search sends ``fuel_type=ELEC`` and ``country=all``. AFDC defaults to
  ``US`` and would silently drop Canadian stations (D1).
- ``api_key`` always goes in the query string; on ``POST`` a key in the form
  body is ignored.
- ``ev_power_kw_min`` is never sent: the stations module filters on known power
  so unknown-power stations stay listed (D10).

Logging rule (D13): one line per request with method, endpoint *template*,
status, duration and the rate-limit headroom. Never the API key, coordinates,
the route, or request URLs (httpx's own request log is silenced in ``app.main``).
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import lru_cache
from typing import Any, Final, Literal

import httpx
from fastapi import status
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import Settings, get_settings
from app.core.geo import LatLon, simplify
from app.integrations.stations import errors
from app.integrations.stations.base import (
    CONNECTOR_ORDER,
    ChargingLevel,
    ConnectorType,
    ProviderStation,
    StationAccess,
    StationConnector,
    StationFilters,
    StationPage,
    StationProvider,
    StationStatus,
)

logger = logging.getLogger(__name__)

PROVIDER: Final[Literal["afdc"]] = "afdc"
KM_PER_MILE = 1.609344
# D6: Douglas-Peucker tolerance for routes sent to nearby-route (≤ 0.1 km after F0).
ROUTE_TOLERANCE_KM = 0.1
# AFDC limits (notes §1.3, §1.3a; docs): radius ≤ 500 mi, corridor ≤ 100 mi.
MAX_RADIUS_MILES = 500.0
MAX_CORRIDOR_MILES = 100.0

# D9: AFDC connector codes → ours. NEMA outlets and J3271 (MCS, trucks) are dropped
# from ``connectors`` but stay in ``raw``.
AFDC_CONNECTORS: Mapping[str, ConnectorType] = {
    "TESLA": "NACS",
    "NACS": "NACS",
    "J1772COMBO": "CCS1",
    "J1772": "J1772",
    "CHADEMO": "CHAdeMO",
}
_FILTER_CONNECTORS: Mapping[ConnectorType, str] = {
    "NACS": "TESLA",
    "CCS1": "J1772COMBO",
    "J1772": "J1772",
    "CHAdeMO": "CHADEMO",
}
_FILTER_LEVELS: Mapping[ChargingLevel, str] = {"level2": "2", "dc_fast": "dc_fast"}
_FILTER_STATUSES: Mapping[str, str] = {
    "open": "E",
    "planned": "P",
    "temporarily_unavailable": "T",
    "all": "all",
}
_STATUSES: Mapping[str, StationStatus] = {
    "E": "open",
    "P": "planned",
    "T": "temporarily_unavailable",
}
_ACCESS: Mapping[str, StationAccess] = {"public": "public", "private": "private"}

_ISO2 = re.compile(r"^[A-Z]{2}$")
_STATION_ID = re.compile(r"^[0-9]{1,12}$")


# ---------------------------------------------------------------------------
# Upstream shapes (only the fields we read; everything else stays in ``raw``)
# ---------------------------------------------------------------------------


class _UpstreamConnector(BaseModel):
    model_config = ConfigDict(extra="ignore")

    port_count: int | None = None
    power_kw: float | None = None


class _UpstreamUnit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connectors: dict[str, _UpstreamConnector] | None = None


class _UpstreamStation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    station_name: str
    latitude: float
    longitude: float
    ev_network: str | None = None
    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None
    ev_connector_types: list[str] | None = None
    ev_charging_units: list[_UpstreamUnit] | None = None
    status_code: str | None = None
    access_code: str | None = None
    access_detail_code: str | None = None
    restricted_access: bool | None = None
    ev_pricing: str | None = None
    updated_at: datetime | None = None
    distance_km: float | None = None


class _UpstreamSearch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fuel_stations: list[dict[str, Any]]
    total_results: int


class _UpstreamStationLookup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    alt_fuel_station: dict[str, Any]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class AfdcProvider:
    """``StationProvider`` backed by the AFDC alt-fuel-stations v1 API.

    Stateless apart from the ``httpx.Client``, so one instance is shared across
    FastAPI's threadpool. Pass ``transport`` (e.g. ``httpx.MockTransport``) in tests.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_base_url: str,
        timeout_seconds: float,
        route_max_points: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if route_max_points < 2:
            raise ValueError("route_max_points must be at least 2.")
        self._api_key = api_key
        self._api_base_url = api_base_url.rstrip("/")
        self._route_max_points = route_max_points
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.BaseTransport | None = None
    ) -> AfdcProvider:
        return cls(
            api_key=settings.afdc_api_key,
            api_base_url=settings.afdc_api_base_url,
            timeout_seconds=settings.stations_timeout_seconds,
            route_max_points=settings.stations_route_max_points,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # StationProvider
    # ------------------------------------------------------------------

    def search_nearby(
        self, lat: float, lon: float, radius_km: float, filters: StationFilters
    ) -> StationPage:
        """Stations within ``radius_km`` of a point, nearest first (AFDC ``nearest``)."""

        _check_point(lat, lon)
        params = {
            **_filter_params(filters),
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
            "radius": _miles(radius_km, maximum=MAX_RADIUS_MILES, name="radius_km"),
        }
        response = self._request("GET", "/nearest.json", endpoint="nearest", params=params)
        return _parse_page(response, filters)

    def search_along_route(
        self, points: Sequence[LatLon], corridor_km: float, filters: StationFilters
    ) -> StationPage:
        """Stations within ``corridor_km`` of a route (AFDC ``nearby-route``).

        ``points`` are ``(lat, lon)``; the route is simplified to at most
        ``route_max_points`` before it's sent (D6). Results come back only roughly
        in route order, so callers must not rely on the order (notes §1.3).
        """

        if len(points) < 2:
            raise ValueError("A route needs at least two points.")
        for lat, lon in points:
            _check_point(lat, lon)
        simplified = simplify(
            points, tolerance_km=ROUTE_TOLERANCE_KM, max_points=self._route_max_points
        )
        form = {
            **_filter_params(filters),
            "distance": _miles(corridor_km, maximum=MAX_CORRIDOR_MILES, name="corridor_km"),
            "route": route_wkt(simplified),
        }
        response = self._request("POST", "/nearby-route.json", endpoint="nearby-route", data=form)
        return _parse_page(response, filters)

    def get(self, external_id: str) -> ProviderStation | None:
        """One station by AFDC ID, or ``None`` if AFDC doesn't know it (HTTP 404).

        Restricted stations are returned (with ``restricted_access=True``) so a
        cached or favorited station can still be shown; only searches drop them.
        """

        if not _STATION_ID.match(external_id):
            return None
        response = self._request(
            "GET", f"/{external_id}.json", endpoint="{id}", params={}, not_found_ok=True
        )
        if response.status_code == status.HTTP_404_NOT_FOUND:
            return None
        try:
            body = _UpstreamStationLookup.model_validate(response.json())
            return normalize_station(body.alt_fuel_station)
        except (ValueError, ValidationError):
            raise errors.unexpected_response() from None

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        endpoint: str,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        not_found_ok: bool = False,
    ) -> httpx.Response:
        if not self._api_key:
            raise errors.not_configured()

        started = time.perf_counter()
        status_code: int | None = None
        remaining: str | None = None
        try:
            response = self._http.request(
                method,
                f"{self._api_base_url}{path}",
                # The key must be in the query string, on POST too (notes §1.1).
                params={**(params or {}), "api_key": self._api_key},
                data=data,
            )
            status_code = response.status_code
            remaining = response.headers.get("x-ratelimit-remaining")
        except httpx.TransportError as exc:
            raise errors.from_transport_error(exc) from None
        finally:
            logger.info(
                "stations_request",
                extra={
                    "provider": PROVIDER,
                    "method": method,
                    "endpoint": endpoint,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "ratelimit_remaining": remaining,
                },
            )

        if not_found_ok and response.status_code == status.HTTP_404_NOT_FOUND:
            return response
        if response.is_error:
            raise errors.from_response(response)
        return response


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------


def _filter_params(filters: StationFilters) -> dict[str, str]:
    params = {
        "fuel_type": "ELEC",
        "country": "all",  # D1: AFDC defaults to US.
        "status": _FILTER_STATUSES[filters.status],
        "access": filters.access,
        "limit": str(filters.limit),
        "offset": str(filters.offset),
    }
    if filters.connector_types:
        params["ev_connector_type"] = ",".join(
            dict.fromkeys(_FILTER_CONNECTORS[c] for c in filters.connector_types)
        )
    if filters.networks:
        params["ev_network"] = ",".join(dict.fromkeys(filters.networks))
    if filters.charging_level is not None:
        params["ev_charging_level"] = _FILTER_LEVELS[filters.charging_level]
    return params


def route_wkt(points: Sequence[LatLon]) -> str:
    """WKT ``LINESTRING`` with **longitude first**, 5 decimals (about 1 m)."""

    return "LINESTRING(" + ", ".join(f"{lon:.5f} {lat:.5f}" for lat, lon in points) + ")"


def _miles(km: float, *, maximum: float, name: str) -> str:
    if not km > 0:
        raise ValueError(f"{name} must be > 0.")
    miles = km / KM_PER_MILE
    if miles > maximum:
        raise ValueError(f"{name} is larger than the provider allows.")
    return f"{miles:.3f}"


def _check_point(lat: float, lon: float) -> None:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordinates out of range.")


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


def _parse_page(response: httpx.Response, filters: StationFilters) -> StationPage:
    try:
        body = _UpstreamSearch.model_validate(response.json())
    except (ValueError, ValidationError):
        raise errors.unexpected_response() from None

    stations: list[ProviderStation] = []
    skipped = 0
    restricted = 0
    for raw in body.fuel_stations:
        try:
            station = normalize_station(raw)
        except ValidationError:
            skipped += 1
            continue
        # AFDC has no filter for this, so it's done here (e.g. dealer-only chargers).
        if station.restricted_access and not filters.include_restricted:
            restricted += 1
            continue
        stations.append(station)
    if skipped:
        # Counts only: station payloads include addresses and coordinates.
        logger.warning(
            "stations_skipped_malformed",
            extra={"provider": PROVIDER, "skipped": skipped, "returned": len(body.fuel_stations)},
        )
    return StationPage(
        stations=stations,
        total_results=body.total_results,
        offset=filters.offset,
        skipped=skipped,
        excluded_restricted=restricted,
    )


def normalize_station(raw: dict[str, Any]) -> ProviderStation:
    """AFDC station JSON → ``ProviderStation``. Raises ``ValidationError`` if malformed."""

    s = _UpstreamStation.model_validate(raw)
    country = (s.country or "").upper()
    return ProviderStation(
        provider=PROVIDER,
        external_id=str(s.id),
        name=s.station_name,
        network=_blank_to_none(s.ev_network),
        street_address=_blank_to_none(s.street_address),
        city=_blank_to_none(s.city),
        region=_blank_to_none(s.state),
        postal_code=_blank_to_none(s.zip),
        country=country if _ISO2.match(country) else None,
        lat=s.latitude,
        lon=s.longitude,
        connectors=normalize_connectors(s.ev_connector_types, s.ev_charging_units),
        access=_ACCESS.get(s.access_code or "", "unknown"),
        access_detail=_blank_to_none(s.access_detail_code),
        restricted_access=bool(s.restricted_access),
        status=_STATUSES.get(s.status_code or "", "unknown"),
        pricing_text=_blank_to_none(s.ev_pricing),
        source_updated_at=s.updated_at,
        provider_distance_km=s.distance_km,
        raw=raw,
    )


def normalize_connectors(
    connector_types: Sequence[str] | None, units: Sequence[_UpstreamUnit] | None
) -> list[StationConnector]:
    """One entry per connector type, at that type's **highest** known power.

    Road trips care about the fastest charging a station offers, so a site with
    4 x 50 kW and 3 x 350 kW CCS1 becomes ``CCS1, count 3, 350 kW``: ``count`` is
    the number of connectors at that maximum power. If no connector of a type
    has a known power, ``power_kw`` is ``None`` and ``count`` is all of them.

    Only connectors with ``port_count > 0`` count: every AFDC unit lists all
    connector types (D9). A type in ``ev_connector_types`` with no unit data
    gets ``count`` and ``power_kw`` of ``None``; counts are never derived from
    the per-level totals.
    """

    counts: dict[ConnectorType, defaultdict[float | None, int]] = {}
    for unit in units or ():
        for afdc_type, connector in (unit.connectors or {}).items():
            ours = AFDC_CONNECTORS.get(afdc_type.upper())
            if ours is None or not connector.port_count or connector.port_count <= 0:
                continue
            counts.setdefault(ours, defaultdict(int))[connector.power_kw] += connector.port_count

    result: list[StationConnector] = []
    for connector_type, by_power in counts.items():
        known = [power for power in by_power if power is not None]
        if known:
            top = max(known)
            result.append(StationConnector(type=connector_type, count=by_power[top], power_kw=top))
        else:
            result.append(StationConnector(type=connector_type, count=sum(by_power.values())))
    for afdc_type in dict.fromkeys(connector_types or ()):
        ours = AFDC_CONNECTORS.get(afdc_type.upper())
        if ours is not None and ours not in counts:
            result.append(StationConnector(type=ours))
            counts[ours] = defaultdict(int)

    return sorted(result, key=lambda c: CONNECTOR_ORDER.index(c.type))


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _default_provider() -> AfdcProvider:
    return AfdcProvider.from_settings(get_settings())


def get_station_provider() -> StationProvider:
    """FastAPI dependency. Tests override it with a fake or a MockTransport provider."""

    return _default_provider()


__all__ = [
    "AFDC_CONNECTORS",
    "AfdcProvider",
    "get_station_provider",
    "normalize_connectors",
    "normalize_station",
    "route_wkt",
]
