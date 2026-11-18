"""Business logic for station search, the ``charging_stations`` cache and compatibility.

phase3_plan.md F5 and decisions D6, D7, D9, D10:

- Searches always go to the provider. Every result is upserted on
  ``(provider, external_id)`` (one statement per provider page), so a station's
  Chargewise ``id`` stays the same across fetches (E5).
- ``get_station`` reads the cache and refreshes a row older than the TTL. If the
  refresh fails, the cached row comes back with ``is_stale: true``.
- ``min_power_kw`` hides stations whose *known* maximum power is lower; stations
  with unknown power stay and are counted in ``unknown_power_count`` (D10).
- Along a route, results are sorted by distance along the route, computed here
  by projecting each station onto the (simplified) route, because the
  provider's order is only approximate (notes §1.3).

``search_along_route`` takes no HTTP types: Phase 4's planner calls it directly.

Logging rule (D13): counts only. Never coordinates, routes or station addresses.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import itertools
import json
import logging
import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

from fastapi import status
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.geo import EARTH_RADIUS_KM, LatLon, haversine_km, simplify
from app.integrations.stations.base import (
    MAX_PAGE_SIZE,
    ConnectorType,
    ProviderStation,
    StationConnector,
    StationFilters,
    StationProvider,
)
from app.modules.stations.models import ChargingStation
from app.modules.stations.schemas import (
    AlongRouteResponse,
    RouteStationOut,
    StationOut,
)
from app.modules.vehicles.garage import get_vehicle_detail
from app.schemas.geo import Coordinates

logger = logging.getLogger(__name__)

# D6: the route is simplified to this tolerance (and the provider's point cap)
# before it's sent and before stations are projected onto it.
ROUTE_TOLERANCE_KM = 0.1
_KNOWN_CONNECTORS: frozenset[str] = frozenset(get_args(ConnectorType))
# Columns an upsert overwrites. ``id``, ``operator`` and ``created_at`` are kept.
_UPSERT_COLUMNS = (
    "name",
    "network",
    "street_address",
    "city",
    "region",
    "postal_code",
    "country",
    "lat",
    "lon",
    "connectors",
    "access",
    "access_detail",
    "status",
    "pricing_text",
    "source_updated_at",
    "fetched_at",
    "raw_payload",
)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NearbyResult:
    items: list[StationOut]
    total_results: int
    unknown_power_count: int
    # Provider offset of the next page, or None on the last page.
    next_offset: int | None


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------


def connectors_for_vehicle(
    db: Session, *, user_id: uuid.UUID, vehicle_id: uuid.UUID
) -> frozenset[ConnectorType]:
    """The connector types of one of the caller's vehicles.

    Goes through the vehicles module (no cross-module query). A vehicle the
    caller doesn't own is ``NOT_FOUND`` (404). Unknown connector strings are
    ignored; an empty set means the vehicle's connectors aren't known.
    """

    detail = get_vehicle_detail(
        db, user_id=user_id, vehicle_id=vehicle_id, stale_after=timedelta(minutes=15)
    )
    if detail.specs is None:
        return frozenset()
    return frozenset(c for c in detail.specs.connectors if c in _KNOWN_CONNECTORS)  # type: ignore[misc]


def _compatible(
    connectors: Iterable[StationConnector], vehicle: frozenset[ConnectorType] | None
) -> bool | None:
    if not vehicle:
        return None
    return any(c.type in vehicle for c in connectors)


def _max_power(connectors: Iterable[StationConnector]) -> float | None:
    known = [c.power_kw for c in connectors if c.power_kw is not None]
    return max(known) if known else None


def _passes_power(station: StationOut, min_power_kw: float | None) -> bool:
    if min_power_kw is None or station.max_power_kw is None:
        return True
    return station.max_power_kw >= min_power_kw


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_nearby(
    db: Session,
    provider: StationProvider,
    *,
    lat: float,
    lon: float,
    radius_km: float,
    filters: StationFilters,
    min_power_kw: float | None = None,
    vehicle_connectors: frozenset[ConnectorType] | None = None,
    now: datetime | None = None,
) -> NearbyResult:
    """One provider page of stations near a point, nearest first, upserted to the cache."""

    page = provider.search_nearby(lat, lon, radius_km, filters)
    returned = len(page.stations) + page.skipped + page.excluded_restricted
    stations = upsert_stations(db, page.stations, now=now)
    items = [
        s.model_copy(update={"compatible": _compatible(s.connectors, vehicle_connectors)})
        for s in stations
        if _passes_power(s, min_power_kw)
    ]
    next_offset = filters.offset + returned
    logger.info(
        "stations_search_nearby",
        extra={"returned": returned, "items": len(items), "total": page.total_results},
    )
    return NearbyResult(
        items=items,
        total_results=page.total_results,
        unknown_power_count=sum(1 for s in items if s.max_power_kw is None),
        next_offset=next_offset if returned and next_offset < page.total_results else None,
    )


def search_along_route(
    db: Session,
    provider: StationProvider,
    *,
    route: Sequence[LatLon],
    corridor_km: float,
    filters: StationFilters,
    min_power_kw: float | None = None,
    vehicle_connectors: frozenset[ConnectorType] | None = None,
    max_results: int = 200,
    route_max_points: int = 1000,
    now: datetime | None = None,
) -> AlongRouteResponse:
    """Stations within ``corridor_km`` of ``route``, sorted by distance along it.

    ``route`` is decoded ``(lat, lon)`` points (at least two). It's simplified
    once (D6: 0.1 km, at most ``route_max_points``) and that simplified line is
    both sent to the provider and used to measure each station's position.

    Provider pages are fetched until every match is collected or
    ``max_results`` stations are (``truncated`` says which). ``filters.limit``
    and ``filters.offset`` are ignored; paging is done here. Every station is
    upserted, so ``items[].id`` are stable Chargewise IDs.
    """

    if len(route) < 2:
        raise ValueError("A route needs at least two points.")
    if max_results < 1:
        raise ValueError("max_results must be at least 1.")
    simplified = simplify(route, tolerance_km=ROUTE_TOLERANCE_KM, max_points=route_max_points)
    if len(simplified) < 2:  # pragma: no cover - simplify keeps both endpoints
        raise ValueError("A route needs at least two points.")

    by_id: dict[uuid.UUID, StationOut] = {}
    offset = 0
    fetched = 0
    total = 0
    while fetched < max_results:
        page_filters = filters.model_copy(
            update={"limit": min(MAX_PAGE_SIZE, max_results - fetched), "offset": offset}
        )
        page = provider.search_along_route(simplified, corridor_km, page_filters)
        total = page.total_results
        returned = len(page.stations) + page.skipped + page.excluded_restricted
        for station in upsert_stations(db, page.stations, now=now):
            by_id[station.id] = station
        fetched += len(page.stations)
        offset += returned
        if returned == 0 or offset >= total:
            break

    segments = _route_segments(simplified)
    items: list[RouteStationOut] = []
    for station in by_id.values():
        if not _passes_power(station, min_power_kw):
            continue
        along_km, from_km = _project(segments, station.location.as_tuple())
        items.append(
            RouteStationOut(
                **station.model_dump(exclude={"compatible", "location"}),
                location=station.location,
                compatible=_compatible(station.connectors, vehicle_connectors),
                distance_along_route_km=round(along_km, 3),
                distance_from_route_km=round(from_km, 3),
            )
        )
    items.sort(key=lambda s: (s.distance_along_route_km, s.distance_from_route_km, str(s.id)))
    logger.info(
        "stations_search_along_route",
        extra={
            "route_points": len(route),
            "simplified_points": len(simplified),
            "items": len(items),
            "total": total,
        },
    )
    return AlongRouteResponse(
        items=items,
        total_results=total,
        unknown_power_count=sum(1 for s in items if s.max_power_kw is None),
        truncated=offset < total,
    )


# ---------------------------------------------------------------------------
# Station detail (cache + TTL refresh, D7)
# ---------------------------------------------------------------------------


def get_station(
    db: Session,
    provider: StationProvider,
    station_id: uuid.UUID,
    *,
    ttl: timedelta,
    vehicle_connectors: frozenset[ConnectorType] | None = None,
    now: datetime | None = None,
) -> StationOut:
    """A cached station, refreshed from the provider if older than ``ttl``.

    If the refresh fails (provider down or not configured) or the provider no
    longer knows the station, the cached row is returned with ``is_stale: true``.
    """

    current = now or datetime.now(UTC)
    row = db.get(ChargingStation, station_id)
    if row is None:
        raise ApiError("NOT_FOUND", "Station not found.", status_code=status.HTTP_404_NOT_FOUND)

    station = _from_row(row, is_stale=False)
    if row.provider != "simulated" and current - row.fetched_at > ttl:
        try:
            fresh = provider.get(row.external_id)
        except ApiError as exc:
            logger.warning("station_refresh_failed", extra={"code": exc.code})
            fresh = None
            station = station.model_copy(update={"is_stale": True})
        else:
            if fresh is None:
                logger.warning("station_refresh_missing")
                station = station.model_copy(update={"is_stale": True})
            else:
                [station] = upsert_stations(db, [fresh], now=current)
    return station.model_copy(
        update={"compatible": _compatible(station.connectors, vehicle_connectors)}
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def upsert_stations(
    db: Session, stations: Sequence[ProviderStation], *, now: datetime | None = None
) -> list[StationOut]:
    """Insert or update ``stations`` in one statement; return them with Chargewise IDs.

    Order follows ``stations``; a station listed twice is returned once.
    """

    unique = list({(s.provider, s.external_id): s for s in stations}.values())
    if not unique:
        return []
    fetched_at = now or datetime.now(UTC)
    rows = [_row_values(s, fetched_at) for s in unique]
    insert = pg_insert(ChargingStation).values(rows)
    stmt = insert.on_conflict_do_update(
        constraint="charging_stations_provider_external_id_uq",
        set_={col: insert.excluded[col] for col in _UPSERT_COLUMNS},
    ).returning(ChargingStation.id, ChargingStation.external_id, ChargingStation.operator)
    saved = {external_id: (id_, operator) for id_, external_id, operator in db.execute(stmt)}
    out: list[StationOut] = []
    for s in unique:
        station_id, operator = saved[s.external_id]
        out.append(
            StationOut(
                id=station_id,
                provider=s.provider,
                external_id=s.external_id,
                name=s.name,
                operator=operator,
                network=s.network,
                street_address=s.street_address,
                city=s.city,
                region=s.region,
                postal_code=s.postal_code,
                country=s.country,
                location=Coordinates(lat=s.lat, lon=s.lon),
                connectors=list(s.connectors),
                max_power_kw=s.max_power_kw,
                access=s.access,
                access_detail=s.access_detail,
                restricted_access=s.restricted_access,
                status=s.status,
                pricing_text=s.pricing_text,
                compatible=None,
                source_updated_at=s.source_updated_at,
                fetched_at=fetched_at,
                is_stale=False,
            )
        )
    return out


def _row_values(s: ProviderStation, fetched_at: datetime) -> dict[str, Any]:
    return {
        "provider": s.provider,
        "external_id": s.external_id,
        "name": s.name,
        "network": s.network,
        "street_address": s.street_address,
        "city": s.city,
        "region": s.region,
        "postal_code": s.postal_code,
        "country": s.country,
        "lat": s.lat,
        "lon": s.lon,
        "connectors": [c.model_dump() for c in s.connectors],
        "access": s.access,
        "access_detail": s.access_detail,
        "status": s.status,
        "pricing_text": s.pricing_text,
        "source_updated_at": s.source_updated_at,
        "fetched_at": fetched_at,
        "raw_payload": s.raw,
        "is_simulated": False,
    }


def _from_row(row: ChargingStation, *, is_stale: bool) -> StationOut:
    connectors = [StationConnector.model_validate(c) for c in row.connectors]
    return StationOut(
        id=row.id,
        provider=row.provider,
        external_id=row.external_id,
        name=row.name,
        operator=row.operator,
        network=row.network,
        street_address=row.street_address,
        city=row.city,
        region=row.region,
        postal_code=row.postal_code,
        country=row.country,
        location=Coordinates(lat=float(row.lat), lon=float(row.lon)),
        connectors=connectors,
        max_power_kw=_max_power(connectors),
        access=row.access,  # type: ignore[arg-type]
        access_detail=row.access_detail,
        # AFDC's flag; the cache keeps it only in the raw payload.
        restricted_access=row.raw_payload.get("restricted_access") is True,
        status=row.status,  # type: ignore[arg-type]
        pricing_text=row.pricing_text,
        compatible=None,
        source_updated_at=row.source_updated_at,
        fetched_at=row.fetched_at,
        is_stale=is_stale,
    )


# ---------------------------------------------------------------------------
# Distance along a route
# ---------------------------------------------------------------------------

# Beyond this, the bounding-box shortcut might miss the nearest segment, so the
# projection falls back to checking every segment.
_SEARCH_MARGIN_KM = 40.0
_KM_PER_DEG_LAT = math.pi / 180 * EARTH_RADIUS_KM


@dataclass(frozen=True)
class _Segment:
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    kx: float  # km per degree of longitude at the segment's mid-latitude
    start_km: float  # distance along the route to the segment's start
    length_km: float
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


def _route_segments(points: Sequence[LatLon]) -> list[_Segment]:
    segments: list[_Segment] = []
    along = 0.0
    margin_lat = _SEARCH_MARGIN_KM / _KM_PER_DEG_LAT
    for (a_lat, a_lon), (b_lat, b_lon) in itertools.pairwise(points):
        length = haversine_km((a_lat, a_lon), (b_lat, b_lon))
        kx = math.cos(math.radians((a_lat + b_lat) / 2)) * _KM_PER_DEG_LAT
        margin_lon = _SEARCH_MARGIN_KM / max(kx, 1e-6)
        segments.append(
            _Segment(
                start_lat=a_lat,
                start_lon=a_lon,
                end_lat=b_lat,
                end_lon=b_lon,
                kx=kx,
                start_km=along,
                length_km=length,
                min_lat=min(a_lat, b_lat) - margin_lat,
                max_lat=max(a_lat, b_lat) + margin_lat,
                min_lon=min(a_lon, b_lon) - margin_lon,
                max_lon=max(a_lon, b_lon) + margin_lon,
            )
        )
        along += length
    return segments


def _segment_projection(seg: _Segment, lat: float, lon: float) -> tuple[float, float]:
    """``(t, distance_km)`` of ``(lat, lon)`` onto ``seg`` (local equirectangular)."""

    ky = _KM_PER_DEG_LAT
    dx = (seg.end_lon - seg.start_lon) * seg.kx
    dy = (seg.end_lat - seg.start_lat) * ky
    px = (lon - seg.start_lon) * seg.kx
    py = (lat - seg.start_lat) * ky
    len_sq = dx * dx + dy * dy
    t = 0.0 if len_sq == 0 else max(0.0, min(1.0, (px * dx + py * dy) / len_sq))
    return t, math.hypot(px - t * dx, py - t * dy)


def _project(segments: Sequence[_Segment], point: LatLon) -> tuple[float, float]:
    """``(distance_along_route_km, distance_from_route_km)`` for ``point``.

    The nearest segment wins; distance along is the route length up to the
    projected point. Ties go to the earlier segment.
    """

    lat, lon = point

    def best(candidates: Iterable[_Segment]) -> tuple[float, float] | None:
        result: tuple[float, float] | None = None
        for seg in candidates:
            t, dist = _segment_projection(seg, lat, lon)
            if result is None or dist < result[1]:
                result = (seg.start_km + t * seg.length_km, dist)
        return result

    nearby = best(
        s for s in segments if s.min_lat <= lat <= s.max_lat and s.min_lon <= lon <= s.max_lon
    )
    if nearby is not None and nearby[1] <= _SEARCH_MARGIN_KM:
        return nearby
    full = best(segments)
    assert full is not None  # at least one segment
    return full


# ---------------------------------------------------------------------------
# Pagination cursor for GET /stations (HMAC-signed provider offset)
# ---------------------------------------------------------------------------

_SIGNATURE_BYTES = 16


def query_digest(parts: dict[str, Any]) -> str:
    """Stable digest of a search's parameters; a cursor only works for the same search."""

    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def encode_cursor(offset: int, *, digest: str, secret: str) -> str | None:
    """``<payload>.<signature>``, or None when no secret is set (paging disabled)."""

    if not secret:
        return None
    payload = json.dumps({"o": offset, "q": digest}, separators=(",", ":")).encode("utf-8")
    body = _b64(payload)
    return f"{body}.{_b64(_sign(body, secret))}"


def decode_cursor(cursor: str, *, digest: str, secret: str) -> int:
    """The provider offset in ``cursor``. Anything invalid is a 422 ``VALIDATION_ERROR``."""

    if not secret:
        raise _invalid_cursor()
    body, _, signature = cursor.partition(".")
    try:
        given = _unb64(signature)
        if not hmac.compare_digest(given, _sign(body, secret)):
            raise _invalid_cursor()
        payload = json.loads(_unb64(body))
    except (ValueError, binascii.Error):
        raise _invalid_cursor() from None
    if not isinstance(payload, dict) or payload.get("q") != digest:
        raise _invalid_cursor()
    offset = payload.get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise _invalid_cursor()
    return offset


def _sign(body: str, secret: str) -> bytes:
    mac = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256)
    return mac.digest()[:_SIGNATURE_BYTES]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not text:
        raise ValueError("empty")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _invalid_cursor() -> ApiError:
    return ApiError(
        "VALIDATION_ERROR",
        "Invalid or expired cursor; start the search again without it.",
        status_code=422,
    )


__all__ = [
    "ROUTE_TOLERANCE_KM",
    "NearbyResult",
    "connectors_for_vehicle",
    "decode_cursor",
    "encode_cursor",
    "get_station",
    "query_digest",
    "search_along_route",
    "search_nearby",
    "upsert_stations",
]
