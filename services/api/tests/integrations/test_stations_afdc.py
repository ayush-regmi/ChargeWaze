"""AFDC stations adapter tests. No network: every provider uses ``httpx.MockTransport``
serving the real responses in ``fixtures/stations/afdc/``."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.geo import decode_polyline
from app.integrations.stations.afdc import (
    AfdcProvider,
    normalize_connectors,
    normalize_station,
    route_wkt,
)
from app.integrations.stations.base import StationConnector, StationFilters

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "fixtures" / "stations" / "afdc"
APP_DIR = Path(__file__).resolve().parents[2] / "app"
API_BASE = "https://afdc.test/api/alt-fuel-stations/v1"
API_KEY = "afdc-test-key-0123456789"
FREDERICK = (39.4143, -77.4105)
TORONTO = (43.6532, -79.3832)

Handler = Callable[[httpx.Request], httpx.Response]


def fixture_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_bytes())


def mapbox_route() -> list[tuple[float, float]]:
    """The real Frederick → Toronto Mapbox route A (9,719 points)."""

    body = json.loads(
        (ROOT / "fixtures" / "mapbox" / "directions_frederick_toronto.json").read_bytes()
    )
    return decode_polyline(body["routes"][0]["geometry"])


class Recorder:
    """Serves one scripted response and records every request."""

    def __init__(self, response: httpx.Response | Callable[[], httpx.Response]) -> None:
        self._response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response() if callable(self._response) else self._response

    @property
    def only(self) -> httpx.Request:
        [request] = self.requests
        return request


def serve(name: str, status_code: int = 200, headers: dict[str, str] | None = None) -> Recorder:
    return Recorder(httpx.Response(status_code, json=fixture_json(name), headers=headers))


def make_provider(handler: Handler, **overrides: Any) -> AfdcProvider:
    kwargs: dict[str, Any] = {
        "api_key": API_KEY,
        "api_base_url": API_BASE,
        "timeout_seconds": 5.0,
        "route_max_points": 1000,
        "transport": httpx.MockTransport(handler),
    }
    kwargs.update(overrides)
    return AfdcProvider(**kwargs)


def query(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


# ---------------------------------------------------------------------------
# search_nearby (nearest.json)
# ---------------------------------------------------------------------------


def test_search_nearby_builds_request_and_parses_fixture() -> None:
    recorder = serve("nearest_frederick_dc_fast.json")
    provider = make_provider(recorder)

    page = provider.search_nearby(
        *FREDERICK, 24.14, StationFilters(charging_level="dc_fast", limit=25)
    )

    request = recorder.only
    assert request.method == "GET"
    assert request.url.path == "/api/alt-fuel-stations/v1/nearest.json"
    assert query(request) == {
        "api_key": API_KEY,
        "fuel_type": "ELEC",
        "country": "all",
        "status": "E",
        "access": "public",
        "limit": "25",
        "offset": "0",
        "ev_charging_level": "dc_fast",
        "latitude": "39.414300",
        "longitude": "-77.410500",
        "radius": "15.000",  # 24.14 km in miles
    }
    assert "ev_power_kw_min" not in query(request)  # D10: power is filtered on our side.

    assert page.total_results == 26
    assert page.offset == 0
    assert page.skipped == 0
    assert page.excluded_restricted == 2
    assert len(page.stations) == 23
    assert {"40025", "449372"}.isdisjoint(s.external_id for s in page.stations)
    first = page.stations[0]
    assert first.provider == "afdc"
    assert first.external_id == "307447"
    assert first.country == "US"
    assert first.status == "open"
    assert first.access == "public"
    assert first.provider_distance_km == pytest.approx(1.68437)
    assert first.connectors == [
        StationConnector(type="CCS1", count=1, power_kw=62.0),
        StationConnector(type="CHAdeMO", count=1, power_kw=62.5),
    ]
    assert first.raw["id"] == 307447
    # nearest.json is sorted by distance (notes §1.3a); the adapter keeps that order.
    distances = [s.provider_distance_km for s in page.stations]
    assert distances == sorted(d for d in distances if d is not None)


def test_country_is_always_all() -> None:
    """D1: AFDC defaults to US and would drop Canadian stations without ``country=all``."""

    recorder = Recorder(lambda: httpx.Response(200, json={"fuel_stations": [], "total_results": 0}))
    provider = make_provider(recorder)

    provider.search_nearby(*TORONTO, 5, StationFilters())
    provider.search_along_route([FREDERICK, TORONTO], 3, StationFilters())

    assert [query(recorder.requests[0])["country"], form(recorder.requests[1])["country"]] == [
        "all",
        "all",
    ]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (StationFilters(), {"status": "E", "access": "public"}),
        (
            StationFilters(connector_types=("NACS", "CCS1", "J1772", "CHAdeMO", "NACS")),
            {"ev_connector_type": "TESLA,J1772COMBO,J1772,CHADEMO"},
        ),
        (
            StationFilters(networks=("Tesla", "Electrify Canada")),
            {"ev_network": "Tesla,Electrify Canada"},
        ),
        (StationFilters(charging_level="level2"), {"ev_charging_level": "2"}),
        (StationFilters(charging_level="dc_fast"), {"ev_charging_level": "dc_fast"}),
        (StationFilters(status="planned", access="all"), {"status": "P", "access": "all"}),
        (StationFilters(status="temporarily_unavailable"), {"status": "T"}),
        (StationFilters(status="all", access="private"), {"status": "all", "access": "private"}),
        (StationFilters(limit=200, offset=400), {"limit": "200", "offset": "400"}),
    ],
)
def test_filter_translation(filters: StationFilters, expected: dict[str, str]) -> None:
    recorder = Recorder(lambda: httpx.Response(200, json={"fuel_stations": [], "total_results": 0}))
    make_provider(recorder).search_nearby(*FREDERICK, 10, filters)

    params = query(recorder.only)
    assert {k: params.get(k) for k in expected} == expected
    if not filters.connector_types:
        assert "ev_connector_type" not in params
    if not filters.networks:
        assert "ev_network" not in params
    if filters.charging_level is None:
        assert "ev_charging_level" not in params


def test_filters_validate_page_size() -> None:
    with pytest.raises(ValueError):
        StationFilters(limit=201)
    with pytest.raises(ValueError):
        StationFilters(limit=0)
    with pytest.raises(ValueError):
        StationFilters(offset=-1)


@pytest.mark.parametrize(
    ("lat", "lon", "radius_km"),
    [(91, 0, 5), (0, 181, 5), (0, 0, 0), (0, 0, -1), (0, 0, 805)],
)
def test_search_nearby_rejects_bad_input_without_calling(
    lat: float, lon: float, radius_km: float
) -> None:
    recorder = Recorder(lambda: httpx.Response(500))
    with pytest.raises(ValueError):
        make_provider(recorder).search_nearby(lat, lon, radius_km, StationFilters())
    assert recorder.requests == []


# ---------------------------------------------------------------------------
# search_along_route (nearby-route.json)
# ---------------------------------------------------------------------------


def test_along_route_posts_simplified_wkt_with_key_in_query() -> None:
    recorder = serve("nearby_route_cross_border_dc_fast.json")
    provider = make_provider(recorder)
    route = mapbox_route()
    assert len(route) > 9000

    provider.search_along_route(route, 3.2187, StationFilters(charging_level="dc_fast", limit=200))

    request = recorder.only
    assert request.method == "POST"
    assert request.url.path == "/api/alt-fuel-stations/v1/nearby-route.json"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    # The key only works in the query string on POST (notes §1.1), and never in the body.
    assert query(request) == {"api_key": API_KEY}
    body = form(request)
    assert API_KEY not in request.content.decode()
    assert body["distance"] == "2.000"
    assert body["country"] == "all"
    assert body["ev_charging_level"] == "dc_fast"
    assert body["limit"] == "200"

    wkt = body["route"]
    assert wkt.startswith("LINESTRING(") and wkt.endswith(")")
    pairs = [p.split(" ") for p in wkt[len("LINESTRING(") : -1].split(", ")]
    assert 2 <= len(pairs) <= 1000
    # Longitude first, endpoints kept.
    assert pairs[0] == [f"{route[0][1]:.5f}", f"{route[0][0]:.5f}"]
    assert pairs[-1] == [f"{route[-1][1]:.5f}", f"{route[-1][0]:.5f}"]


def test_along_route_respects_point_cap() -> None:
    recorder = Recorder(lambda: httpx.Response(200, json={"fuel_stations": [], "total_results": 0}))
    make_provider(recorder, route_max_points=50).search_along_route(
        mapbox_route(), 3, StationFilters()
    )
    assert len(form(recorder.only)["route"].split(", ")) <= 50


def test_route_wkt_is_longitude_first() -> None:
    assert route_wkt([(39.41430, -77.41050), (43.65320, -79.38320)]) == (
        "LINESTRING(-77.41050 39.41430, -79.38320 43.65320)"
    )


def test_cross_border_fixture_has_us_and_canadian_stations() -> None:
    """F3 acceptance: the F0 E2 fixture parses into both US and CA stations."""

    page = make_provider(serve("nearby_route_cross_border_dc_fast.json")).search_along_route(
        [FREDERICK, TORONTO], 3, StationFilters(charging_level="dc_fast", limit=200)
    )

    assert page.skipped == 0
    assert page.total_results == 122
    assert page.excluded_restricted == 3
    assert Counter(s.country for s in page.stations) == {"US": 39, "CA": 80}
    ontario = [s for s in page.stations if s.country == "CA"]
    assert {s.region for s in ontario} == {"ON"}
    assert all(s.connectors for s in page.stations)


@pytest.mark.parametrize(
    ("points", "corridor_km"),
    [
        ([FREDERICK], 3),
        ([FREDERICK, TORONTO], 0),
        ([FREDERICK, TORONTO], 161),
        ([FREDERICK, (95.0, -79.0)], 3),
    ],
)
def test_along_route_rejects_bad_input_without_calling(
    points: list[tuple[float, float]], corridor_km: float
) -> None:
    recorder = Recorder(lambda: httpx.Response(500))
    with pytest.raises(ValueError):
        make_provider(recorder).search_along_route(points, corridor_km, StationFilters())
    assert recorder.requests == []


def test_pagination_reports_total_and_offset() -> None:
    recorder = serve("nearby_route_all_levels_first_page.json")
    page = make_provider(recorder).search_along_route(
        [FREDERICK, TORONTO], 3, StationFilters(limit=25, offset=50)
    )

    assert form(recorder.only)["offset"] == "50"
    assert page.offset == 50
    assert page.total_results == 955
    assert page.excluded_restricted == 3
    assert len(page.stations) == 22


def test_malformed_station_is_skipped_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    body = fixture_json("nearest_frederick_dc_fast.json")
    body["fuel_stations"][3].pop("latitude")  # Station 3 isn't one of the restricted ones.
    caplog.set_level(logging.WARNING, logger="app.integrations.stations")

    page = make_provider(Recorder(httpx.Response(200, json=body))).search_nearby(
        *FREDERICK, 10, StationFilters()
    )

    assert len(page.stations) == 22
    assert page.skipped == 1
    [record] = [r for r in caplog.records if r.getMessage() == "stations_skipped_malformed"]
    assert record.__dict__["skipped"] == 1


@pytest.mark.parametrize(
    "body",
    [{"total_results": 1}, {"fuel_stations": None, "total_results": 0}, ["not", "an", "object"]],
)
def test_malformed_search_response_is_502(body: Any) -> None:
    with pytest.raises(ApiError) as exc:
        make_provider(Recorder(httpx.Response(200, json=body))).search_nearby(
            *FREDERICK, 10, StationFilters()
        )
    assert (exc.value.code, exc.value.status_code) == ("STATIONS_UNREACHABLE", 502)


def test_non_json_response_is_502() -> None:
    with pytest.raises(ApiError) as exc:
        make_provider(Recorder(httpx.Response(200, text="<html>"))).search_nearby(
            *FREDERICK, 10, StationFilters()
        )
    assert exc.value.status_code == 502


# ---------------------------------------------------------------------------
# get (station by ID)
# ---------------------------------------------------------------------------


def test_get_parses_canadian_station() -> None:
    recorder = serve("station_448148.json")
    station = make_provider(recorder).get("448148")

    assert recorder.only.url.path == "/api/alt-fuel-stations/v1/448148.json"
    assert query(recorder.only) == {"api_key": API_KEY}
    assert station is not None
    assert station.model_dump(exclude={"raw", "source_updated_at"}) == {
        "provider": "afdc",
        "external_id": "448148",
        "name": "Woodview Place (Burlington, ON)",
        "network": "Electrify Canada",
        "street_address": "3295 Fairview Street",
        "city": "Burlington",
        "region": "ON",
        "postal_code": "L7N 3N9",
        "country": "CA",
        "lat": 43.3541,
        "lon": -79.7862,
        "connectors": [{"type": "CCS1", "count": 10, "power_kw": 350.0}],
        "access": "public",
        "access_detail": None,
        "restricted_access": False,
        "status": "open",
        "pricing_text": None,
        "provider_distance_km": None,
    }
    assert station.source_updated_at is not None
    assert station.source_updated_at.isoformat() == "2026-11-17T03:58:28+00:00"


def test_get_unknown_station_returns_none() -> None:
    assert make_provider(serve("station_not_found.json", 404)).get("999999999") is None


@pytest.mark.parametrize("external_id", ["", "abc", "1/../2", "12.json?x=1", "1" * 13])
def test_get_rejects_non_numeric_ids_without_calling(external_id: str) -> None:
    recorder = Recorder(lambda: httpx.Response(500))
    assert make_provider(recorder).get(external_id) is None
    assert recorder.requests == []


def test_get_malformed_body_is_502() -> None:
    with pytest.raises(ApiError) as exc:
        make_provider(Recorder(httpx.Response(200, json={"alt_fuel_station": {"id": 1}}))).get("1")
    assert exc.value.status_code == 502


# ---------------------------------------------------------------------------
# Normalization: connectors (D9), status/access (D8)
# ---------------------------------------------------------------------------


def _station(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "station_name": "Test",
        "latitude": 39.4,
        "longitude": -77.4,
        "country": "US",
    }
    base.update(overrides)
    return base


def _unit(**connectors: tuple[int, float | None]) -> dict[str, Any]:
    every = ("CHADEMO", "J1772", "J1772COMBO", "J3271", "NEMA1450", "NEMA515", "NEMA520", "TESLA")
    return {
        "charging_level": "dc_fast",
        "connectors": {
            t: {
                "port_count": connectors.get(t, (0, None))[0],
                "power_kw": connectors.get(t, (0, None))[1],
            }
            for t in every
        },
    }


def test_connectors_take_each_types_max_power_across_units() -> None:
    station = normalize_station(
        _station(
            ev_connector_types=["J1772COMBO", "TESLA", "NEMA520", "J3271"],
            ev_charging_units=[
                _unit(J1772COMBO=(2, 350.0)),
                _unit(J1772COMBO=(4, 50.0), TESLA=(8, 250.0)),
                _unit(J1772COMBO=(1, 350.0), NEMA520=(1, 2.4), J3271=(1, 1000.0)),
            ],
        )
    )

    # One entry per type at its fastest power; count is the ports at that power.
    # NEMA and J3271 are dropped; zero-port entries ignored.
    assert station.connectors == [
        StationConnector(type="NACS", count=8, power_kw=250.0),
        StationConnector(type="CCS1", count=3, power_kw=350.0),
    ]
    assert station.connector_types == {"NACS", "CCS1"}
    assert station.max_power_kw == 350.0
    # Dropped types are still in the raw payload.
    assert "NEMA520" in station.raw["ev_connector_types"]


def test_connectors_without_unit_data_stay_null() -> None:
    """Counts are never derived from level totals (D9)."""

    station = normalize_station(
        _station(
            ev_connector_types=["J1772", "CHADEMO", "NEMA515"],
            ev_charging_units=None,
            ev_level2_evse_num=6,
            ev_dc_fast_num=2,
        )
    )
    assert station.connectors == [
        StationConnector(type="CHAdeMO", count=None, power_kw=None),
        StationConnector(type="J1772", count=None, power_kw=None),
    ]
    assert station.max_power_kw is None


def test_type_listed_but_missing_from_units_gets_a_null_entry() -> None:
    station = normalize_station(
        _station(
            ev_connector_types=["J1772COMBO", "CHADEMO"],
            ev_charging_units=[_unit(J1772COMBO=(2, 150.0))],
        )
    )
    assert station.connectors == [
        StationConnector(type="CCS1", count=2, power_kw=150.0),
        StationConnector(type="CHAdeMO", count=None, power_kw=None),
    ]


def test_unknown_power_does_not_beat_known_power() -> None:
    station = normalize_station(
        _station(
            ev_connector_types=["J1772COMBO", "J1772"],
            ev_charging_units=[
                _unit(J1772COMBO=(2, None), J1772=(6, None)),
                _unit(J1772COMBO=(1, 62.5)),
            ],
        )
    )
    assert station.connectors == [
        StationConnector(type="CCS1", count=1, power_kw=62.5),
        # No J1772 power known anywhere: count is all of them, power stays null.
        StationConnector(type="J1772", count=6, power_kw=None),
    ]


def test_each_connector_type_appears_once_in_real_fixtures() -> None:
    for name in (
        "nearby_route_cross_border_dc_fast.json",
        "nearby_route_all_levels_first_page.json",
        "nearest_frederick_dc_fast.json",
    ):
        for raw in fixture_json(name)["fuel_stations"]:
            types = [c.type for c in normalize_station(raw).connectors]
            assert len(types) == len(set(types)), raw["id"]


def test_real_station_with_counts_but_unknown_power() -> None:
    """Station 88690 in the E2 fixture: ports known, power not. Power stays null."""

    body = fixture_json("nearby_route_cross_border_dc_fast.json")
    [raw] = [s for s in body["fuel_stations"] if s["id"] == 88690]
    station = normalize_station(raw)

    assert station.connectors == [
        StationConnector(type="CCS1", count=1, power_kw=None),
        StationConnector(type="CHAdeMO", count=1, power_kw=None),
    ]
    assert station.max_power_kw is None


def test_unknown_connector_types_are_ignored_but_kept_in_raw() -> None:
    connectors = normalize_connectors(["WIRELESS", "J1772"], None)
    assert connectors == [StationConnector(type="J1772")]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("E", "open"),
        ("P", "planned"),
        ("T", "temporarily_unavailable"),
        ("X", "unknown"),
        (None, "unknown"),
    ],
)
def test_status_mapping(code: str | None, expected: str) -> None:
    assert normalize_station(_station(status_code=code)).status == expected


@pytest.mark.parametrize(
    ("fields", "access", "detail"),
    [
        ({"access_code": "public"}, "public", None),
        ({"access_code": "private", "access_detail_code": "FLEET"}, "private", "FLEET"),
        (
            {"access_code": "public", "access_detail_code": "CREDIT_CARD_ALWAYS"},
            "public",
            "CREDIT_CARD_ALWAYS",
        ),
        ({"access_code": "public", "restricted_access": True}, "public", None),
        ({"access_code": None}, "unknown", None),
        ({"access_code": "weird"}, "unknown", None),
    ],
)
def test_access_mapping(fields: dict[str, Any], access: str, detail: str | None) -> None:
    station = normalize_station(_station(**fields))
    assert (station.access, station.access_detail) == (access, detail)
    assert station.restricted_access is bool(fields.get("restricted_access"))


def _search_body() -> dict[str, Any]:
    return {
        "total_results": 3,
        "fuel_stations": [
            _station(id=1),
            _station(id=2, restricted_access=True),
            _station(id=3, restricted_access=None),
        ],
    }


def test_searches_drop_restricted_stations_by_default() -> None:
    provider = make_provider(Recorder(lambda: httpx.Response(200, json=_search_body())))

    for page in (
        provider.search_nearby(*FREDERICK, 10, StationFilters()),
        provider.search_along_route([FREDERICK, TORONTO], 3, StationFilters()),
    ):
        assert [s.external_id for s in page.stations] == ["1", "3"]
        assert page.excluded_restricted == 1
        assert page.total_results == 3  # AFDC's count; it can't filter this itself.


def test_include_restricted_keeps_them() -> None:
    provider = make_provider(Recorder(lambda: httpx.Response(200, json=_search_body())))
    page = provider.search_nearby(*FREDERICK, 10, StationFilters(include_restricted=True))

    assert [s.external_id for s in page.stations] == ["1", "2", "3"]
    assert page.excluded_restricted == 0
    assert page.stations[1].restricted_access is True


def test_include_restricted_is_not_sent_to_afdc() -> None:
    recorder = Recorder(lambda: httpx.Response(200, json=_search_body()))
    make_provider(recorder).search_nearby(*FREDERICK, 10, StationFilters())
    assert not any("restricted" in key for key in query(recorder.only))


def test_get_still_returns_a_restricted_station() -> None:
    """A cached or favorited station must stay viewable; only searches drop them."""

    body = {"alt_fuel_station": _station(id=40025, restricted_access=True)}
    station = make_provider(Recorder(httpx.Response(200, json=body))).get("40025")
    assert station is not None
    assert station.restricted_access is True


def test_text_fields_are_cleaned_not_invented() -> None:
    station = normalize_station(
        _station(
            country="ca",
            ev_network="  ",
            ev_pricing="$0.48/kWh",
            zip="",
            updated_at=None,
        )
    )
    assert station.country == "CA"
    assert station.network is None
    assert station.postal_code is None
    assert station.pricing_text == "$0.48/kWh"
    assert station.source_updated_at is None
    assert normalize_station(_station(country="USA")).country is None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_missing_key_is_not_configured_without_calling() -> None:
    recorder = Recorder(lambda: httpx.Response(200))
    with pytest.raises(ApiError) as exc:
        make_provider(recorder, api_key="").search_nearby(*FREDERICK, 10, StationFilters())
    assert (exc.value.code, exc.value.status_code) == ("STATIONS_NOT_CONFIGURED", 503)
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("response", "code", "status_code"),
    [
        (
            httpx.Response(403, json=fixture_json("error_api_key_missing.json")),
            "STATIONS_NOT_CONFIGURED",
            503,
        ),
        (httpx.Response(401, json={}), "STATIONS_NOT_CONFIGURED", 503),
        (httpx.Response(422, json={"errors": ["bad"]}), "STATIONS_UNREACHABLE", 502),
        (
            httpx.Response(429, json={"error": {"code": "OVER_RATE_LIMIT"}}),
            "STATIONS_UNREACHABLE",
            502,
        ),
        (httpx.Response(500, text="oops"), "STATIONS_UNREACHABLE", 502),
        (httpx.Response(503, text="down"), "STATIONS_UNREACHABLE", 502),
    ],
)
def test_http_error_mapping(response: httpx.Response, code: str, status_code: int) -> None:
    with pytest.raises(ApiError) as exc:
        make_provider(Recorder(response)).search_along_route(
            [FREDERICK, TORONTO], 3, StationFilters()
        )
    assert (exc.value.code, exc.value.status_code) == (code, status_code)
    if status_code == 502:
        assert exc.value.details == {"upstream_status": response.status_code}
    # Provider bodies never leak into our error.
    assert "OVER_RATE_LIMIT" not in exc.value.message


def test_get_server_error_is_not_treated_as_missing() -> None:
    with pytest.raises(ApiError) as exc:
        make_provider(Recorder(httpx.Response(500))).get("448148")
    assert exc.value.status_code == 502


@pytest.mark.parametrize(
    ("exc_type", "status_code"),
    [(httpx.ReadTimeout, 504), (httpx.ConnectTimeout, 504), (httpx.ConnectError, 502)],
)
def test_transport_error_mapping(exc_type: type[httpx.TransportError], status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("boom", request=request)

    with pytest.raises(ApiError) as exc:
        make_provider(handler).search_nearby(*FREDERICK, 10, StationFilters())
    assert (exc.value.code, exc.value.status_code) == ("STATIONS_UNREACHABLE", status_code)


# ---------------------------------------------------------------------------
# Settings, logging, isolation
# ---------------------------------------------------------------------------


def test_from_settings_uses_afdc_settings() -> None:
    settings = Settings(
        afdc_api_key=API_KEY,
        afdc_api_base_url=API_BASE + "/",
        stations_timeout_seconds=3,
        stations_route_max_points=2,
    )
    recorder = Recorder(lambda: httpx.Response(200, json={"fuel_stations": [], "total_results": 0}))
    provider = AfdcProvider.from_settings(settings, transport=httpx.MockTransport(recorder))
    provider.search_along_route(mapbox_route(), 3, StationFilters())

    request = recorder.only
    assert str(request.url).startswith(API_BASE + "/nearby-route.json?")
    assert len(form(request)["route"].split(", ")) == 2


def test_default_settings_point_at_the_new_host() -> None:
    settings = Settings()
    assert settings.afdc_api_base_url == "https://developer.nlr.gov/api/alt-fuel-stations/v1"
    assert settings.stations_route_max_points == 1000


def test_logs_have_no_key_coordinates_or_route(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="app.integrations.stations")

    def handler(request: httpx.Request) -> httpx.Response:
        name = (
            "nearest_frederick_dc_fast.json"
            if "nearest" in request.url.path
            else "nearby_route_cross_border_dc_fast.json"
            if "nearby-route" in request.url.path
            else "station_448148.json"
        )
        return httpx.Response(
            200, json=fixture_json(name), headers={"X-Ratelimit-Remaining": "987"}
        )

    provider = make_provider(handler)
    provider.search_nearby(*FREDERICK, 10, StationFilters())
    provider.search_along_route(mapbox_route(), 3, StationFilters())
    provider.get("448148")

    records = [r for r in caplog.records if r.name.startswith("app.integrations.stations")]
    assert [r.getMessage() for r in records] == ["stations_request"] * 3
    assert [(r.__dict__["method"], r.__dict__["endpoint"]) for r in records] == [
        ("GET", "nearest"),
        ("POST", "nearby-route"),
        ("GET", "{id}"),
    ]
    for record in records:
        assert record.__dict__["provider"] == "afdc"
        assert record.__dict__["status"] == 200
        assert record.__dict__["ratelimit_remaining"] == "987"

    standard = set(vars(logging.LogRecord("x", 0, "", 0, "", None, None)))
    dumped = " ".join(
        json.dumps(
            {"msg": r.getMessage(), **{k: v for k, v in vars(r).items() if k not in standard}},
            default=str,
        )
        for r in caplog.records
    )
    for forbidden in (API_KEY, "LINESTRING", "-77.41", "39.41", "448148", "Burlington"):
        assert forbidden not in dumped
    assert not re.search(r"\d\.\d{3,}", dumped)


def test_failed_request_is_logged_without_status(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.integrations.stations")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ApiError):
        make_provider(handler).get("1")
    [record] = [r for r in caplog.records if r.getMessage() == "stations_request"]
    assert record.__dict__["status"] is None


def test_only_the_adapter_talks_to_afdc() -> None:
    """Nothing outside ``integrations/stations`` (and the settings default) names AFDC's API."""

    pattern = re.compile(
        r"developer\.(?:nlr|nrel)\.gov|alt-fuel-stations|nearby-route\.json|nearest\.json"
    )
    allowed = {APP_DIR / "core" / "config.py"}
    offenders = [
        path.relative_to(APP_DIR)
        for path in APP_DIR.rglob("*.py")
        if "integrations/stations" not in path.as_posix()
        and path not in allowed
        and pattern.search(path.read_text())
    ]
    assert offenders == []
