"""Unit tests for ``app.modules.stations.service``: route projection, the cursor,
and ``search_along_route`` called directly (the way Phase 4's planner will)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.geo import LatLon, haversine_km
from app.integrations.stations.base import (
    ProviderStation,
    StationConnector,
    StationFilters,
    StationPage,
)
from app.modules.stations import service

# A straight-ish line due north from Frederick, about 111 km.
START: LatLon = (39.0, -77.0)
END: LatLon = (40.0, -77.0)


def _station(external_id: str, lat: float, lon: float, **kw: Any) -> ProviderStation:
    return ProviderStation(
        provider="afdc",
        external_id=external_id,
        name=f"Station {external_id}",
        country="US",
        lat=lat,
        lon=lon,
        connectors=kw.pop("connectors", [StationConnector(type="CCS1", count=4, power_kw=150)]),
        raw={"id": external_id},
        **kw,
    )


class FakeProvider:
    """A ``StationProvider`` with no HTTP: returns ``stations`` page by page."""

    def __init__(self, stations: list[ProviderStation]) -> None:
        self.stations = stations
        self.calls: list[tuple[list[LatLon], float, StationFilters]] = []

    def search_nearby(
        self, lat: float, lon: float, radius_km: float, filters: StationFilters
    ) -> StationPage:  # pragma: no cover - not used here
        raise NotImplementedError

    def search_along_route(
        self, points: Sequence[LatLon], corridor_km: float, filters: StationFilters
    ) -> StationPage:
        self.calls.append((list(points), corridor_km, filters))
        page = self.stations[filters.offset : filters.offset + filters.limit]
        return StationPage(stations=page, total_results=len(self.stations), offset=filters.offset)

    def get(self, external_id: str) -> ProviderStation | None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# search_along_route as a library call (P4-F4)
# ---------------------------------------------------------------------------


def test_search_along_route_sorts_by_position_along_the_route(db: Session) -> None:
    # Provider order is deliberately scrambled.
    provider = FakeProvider(
        [
            _station("far", 39.9, -76.99),
            _station("near", 39.1, -77.01),
            _station("middle", 39.5, -77.0),
        ]
    )
    result = service.search_along_route(
        db,
        provider,
        route=[START, (39.5, -77.0), END],
        corridor_km=3,
        filters=StationFilters(charging_level="dc_fast"),
    )
    assert [i.external_id for i in result.items] == ["near", "middle", "far"]
    near, middle, far = result.items
    assert near.distance_along_route_km == pytest.approx(
        haversine_km(START, (39.1, -77.0)), abs=0.1
    )
    assert middle.distance_from_route_km == pytest.approx(0.0, abs=0.01)
    assert far.distance_from_route_km == pytest.approx(0.855, abs=0.05)  # 0.01° lon at 40°N
    assert result.truncated is False
    [(_points, corridor, filters)] = provider.calls
    assert corridor == 3
    assert filters.charging_level == "dc_fast"
    assert filters.offset == 0


def test_search_along_route_simplifies_before_calling_the_provider(db: Session) -> None:
    # 1,001 collinear points collapse to the two endpoints.
    route = [(39.0 + i / 1000, -77.0) for i in range(1001)]
    provider = FakeProvider([_station("a", 39.5, -77.0)])
    result = service.search_along_route(
        db, provider, route=route, corridor_km=3, filters=StationFilters()
    )
    [(points, _, _)] = provider.calls
    assert points == [route[0], route[-1]]
    assert result.items[0].distance_along_route_km == pytest.approx(55.6, abs=0.2)


def test_search_along_route_respects_the_point_cap(db: Session) -> None:
    # A zig-zag that Douglas-Peucker can't simplify at 0.1 km.
    route = [(39.0 + i / 100, -77.0 + (0.05 if i % 2 else 0)) for i in range(101)]
    provider = FakeProvider([])
    service.search_along_route(
        db, provider, route=route, corridor_km=3, filters=StationFilters(), route_max_points=20
    )
    [(points, _, _)] = provider.calls
    assert 2 <= len(points) <= 20


def test_search_along_route_upserts_and_returns_stable_ids(db: Session) -> None:
    provider = FakeProvider([_station("a", 39.5, -77.0)])
    first = service.search_along_route(
        db, provider, route=[START, END], corridor_km=3, filters=StationFilters()
    )
    second = service.search_along_route(
        db, provider, route=[START, END], corridor_km=3, filters=StationFilters()
    )
    assert first.items[0].id == second.items[0].id


def test_search_along_route_caps_results(db: Session) -> None:
    stations = [_station(str(i), 39.0 + i / 1000, -77.0) for i in range(450)]
    provider = FakeProvider(stations)
    result = service.search_along_route(
        db, provider, route=[START, END], corridor_km=3, filters=StationFilters(), max_results=300
    )
    assert [(f.offset, f.limit) for _, _, f in provider.calls] == [(0, 200), (200, 100)]
    assert len(result.items) == 300
    assert result.truncated is True
    assert result.total_results == 450


def test_power_filter_and_compatibility(db: Session) -> None:
    provider = FakeProvider(
        [
            _station("slow", 39.1, -77.0, connectors=[StationConnector(type="J1772", power_kw=7)]),
            _station("unknown", 39.2, -77.0, connectors=[StationConnector(type="NACS")]),
            _station("fast", 39.3, -77.0),
        ]
    )
    result = service.search_along_route(
        db,
        provider,
        route=[START, END],
        corridor_km=3,
        filters=StationFilters(),
        min_power_kw=50,
        vehicle_connectors=frozenset({"NACS"}),
    )
    assert [(i.external_id, i.compatible) for i in result.items] == [
        ("unknown", True),
        ("fast", False),
    ]
    assert result.unknown_power_count == 1


def test_search_along_route_rejects_a_single_point(db: Session) -> None:
    with pytest.raises(ValueError, match="two points"):
        service.search_along_route(
            db, FakeProvider([]), route=[START], corridor_km=3, filters=StationFilters()
        )


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def test_cursor_round_trip() -> None:
    digest = service.query_digest({"lat": 39.4, "limit": 10})
    cursor = service.encode_cursor(40, digest=digest, secret="s3cret")
    assert cursor is not None
    assert service.decode_cursor(cursor, digest=digest, secret="s3cret") == 40


@pytest.mark.parametrize(
    ("secret", "digest"),
    [("other-secret", None), ("s3cret", "different-query")],
)
def test_cursor_rejects_wrong_secret_or_query(secret: str, digest: str | None) -> None:
    good = service.query_digest({"lat": 39.4})
    cursor = service.encode_cursor(40, digest=good, secret="s3cret")
    assert cursor is not None
    with pytest.raises(ApiError) as exc:
        service.decode_cursor(cursor, digest=digest or good, secret=secret)
    assert exc.value.status_code == 422


def test_cursor_rejects_a_forged_payload() -> None:
    digest = service.query_digest({})
    forged = service._b64(b'{"o":-5,"q":"' + digest.encode() + b'"}')
    cursor = f"{forged}.{service._b64(service._sign(forged, 'k'))}"
    with pytest.raises(ApiError):
        service.decode_cursor(cursor, digest=digest, secret="k")
    other = service._b64(b"[1, 2]")
    with pytest.raises(ApiError):
        service.decode_cursor(
            f"{other}.{service._b64(service._sign(other, 'k'))}", digest=digest, secret="k"
        )


def test_no_secret_means_no_cursor() -> None:
    assert service.encode_cursor(10, digest="d", secret="") is None
    with pytest.raises(ApiError):
        service.decode_cursor("a.b", digest="d", secret="")


def test_query_digest_is_order_independent() -> None:
    assert service.query_digest({"a": 1, "b": 2}) == service.query_digest({"b": 2, "a": 1})
    assert service.query_digest({"a": 1}) != service.query_digest({"a": 2})
