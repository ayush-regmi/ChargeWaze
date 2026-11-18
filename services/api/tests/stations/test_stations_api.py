"""Stations API tests (Phase 3 F5): ``GET /stations``, ``GET /stations/{id}``,
``POST /stations/along-route`` and the ``charging_stations`` cache.

Acceptance (phase3_plan.md §1): E2 (cross-border stations through the endpoint),
E5 (cache upsert with stable IDs), E6 (provider failures map to the envelope).
"""

from __future__ import annotations

import copy
import itertools
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.geo import encode_polyline
from app.integrations.stations.afdc import normalize_station
from app.modules.stations.models import ChargingStation
from app.modules.stations.schemas import MAX_GEOMETRY_BYTES
from tests.conftest import TEST_USER_ID
from tests.stations.conftest import (
    OTHER_USER_ID,
    FakeAfdc,
    afdc_json,
    paged,
    route_a_points,
    route_a_polyline,
)

STATIONS = "/api/v1/stations"
ALONG = "/api/v1/stations/along-route"
FREDERICK = {"lat": 39.4143, "lon": -77.4105}
Auth = Callable[..., dict[str, str]]


def _station_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(ChargingStation)) or 0


def _nearby(api: TestClient, auth: Auth, **params: Any) -> httpx.Response:
    return api.get(STATIONS, params={**FREDERICK, **params}, headers=auth())


def _along(api: TestClient, auth: Auth, **body: Any) -> httpx.Response:
    return api.post(ALONG, json={"geometry_polyline6": route_a_polyline(), **body}, headers=auth())


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", STATIONS, {"params": FREDERICK}),
        ("GET", f"{STATIONS}/{uuid.uuid4()}", {}),
        ("POST", ALONG, {"json": {"geometry_polyline6": "_p~iF~ps|U_ulLnnqC"}}),
    ],
)
def test_endpoints_require_auth(
    api: TestClient, afdc: FakeAfdc, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    response = api.request(method, path, **kwargs)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert afdc.requests == []


# ---------------------------------------------------------------------------
# GET /stations + cache (E5)
# ---------------------------------------------------------------------------


def test_nearby_returns_cached_stations_with_stable_ids(
    api: TestClient, auth: Auth, afdc: FakeAfdc, db: Session
) -> None:
    first = _nearby(api, auth)
    assert first.status_code == 200, first.text
    body = first.json()
    raw = afdc_json("nearest_frederick_dc_fast.json")["fuel_stations"]
    expected = [s for s in map(normalize_station, raw) if not s.restricted_access]
    assert [i["external_id"] for i in body["items"]] == [s.external_id for s in expected]
    assert body["total_results"] == 26
    assert _station_count(db) == len(expected)

    item = body["items"][0]
    uuid.UUID(item["id"])
    assert item["provider"] == "afdc"
    assert item["live_availability"] is None
    assert item["is_stale"] is False
    assert item["fetched_at"]
    assert item["compatible"] is None
    assert set(item["location"]) == {"lat", "lon"}
    for connector in item["connectors"]:
        assert set(connector) == {"type", "count", "power_kw"}

    second = _nearby(api, auth).json()
    assert {i["external_id"]: i["id"] for i in second["items"]} == {
        i["external_id"]: i["id"] for i in body["items"]
    }
    assert _station_count(db) == len(expected)


def test_changed_provider_data_updates_the_cached_row(
    api: TestClient, auth: Auth, afdc: FakeAfdc, db: Session
) -> None:
    first = _nearby(api, auth).json()["items"][0]

    changed = afdc_json("nearest_frederick_dc_fast.json")
    target = next(s for s in changed["fuel_stations"] if str(s["id"]) == first["external_id"])
    target["station_name"] = "Renamed Station"
    target["ev_pricing"] = "$0.48/kWh"
    afdc.nearest = paged(changed)

    second = _nearby(api, auth).json()["items"][0]
    assert second["id"] == first["id"]
    assert second["name"] == "Renamed Station"
    row = db.get(ChargingStation, uuid.UUID(first["id"]))
    assert row is not None
    db.refresh(row)
    assert row.name == "Renamed Station"
    assert row.pricing_text == "$0.48/kWh"
    assert row.raw_payload["station_name"] == "Renamed Station"


def test_nearby_sends_country_all_and_filters(api: TestClient, auth: Auth, afdc: FakeAfdc) -> None:
    response = _nearby(
        api,
        auth,
        radius_km=16.09344,
        connector=["CCS1", "CHAdeMO"],
        network=["Electrify America"],
        charging_level="dc_fast",
        limit=10,
    )
    assert response.status_code == 200, response.text
    [params] = afdc.params
    assert params["country"] == "all"
    assert params["radius"] == "10.000"
    assert params["ev_connector_type"] == "J1772COMBO,CHADEMO"
    assert params["ev_network"] == "Electrify America"
    assert params["ev_charging_level"] == "dc_fast"
    assert params["limit"] == "10"
    assert params["offset"] == "0"
    assert "ev_power_kw_min" not in params


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 91, "lon": 0},
        {"lat": 0, "lon": 181},
        {"radius_km": 81},
        {"radius_km": 0},
        {"limit": 201},
        {"connector": "TYPE2"},
        {"min_power_kw": -1},
    ],
)
def test_nearby_validation(
    api: TestClient, auth: Auth, afdc: FakeAfdc, params: dict[str, Any]
) -> None:
    response = api.get(STATIONS, params={**FREDERICK, **params}, headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert afdc.requests == []


def test_nearby_requires_a_point(api: TestClient, auth: Auth) -> None:
    assert api.get(STATIONS, headers=auth()).status_code == 422


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def test_cursor_pages_through_provider_offsets(api: TestClient, auth: Auth, afdc: FakeAfdc) -> None:
    first = _nearby(api, auth, limit=10).json()
    assert first["next_cursor"]
    second = _nearby(api, auth, limit=10, cursor=first["next_cursor"]).json()
    assert afdc.params[1]["offset"] == "10"
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    third = _nearby(api, auth, limit=10, cursor=second["next_cursor"]).json()
    assert afdc.params[2]["offset"] == "20"
    # AFDC says 26 in total but the fixture keeps 25, so offset 25 comes back
    # empty, and an empty page always ends paging.
    fourth = _nearby(api, auth, limit=10, cursor=third["next_cursor"]).json()
    assert afdc.params[3]["offset"] == "25"
    assert fourth["items"] == []
    assert fourth["next_cursor"] is None


def test_last_page_has_no_cursor(api: TestClient, auth: Auth, afdc: FakeAfdc) -> None:
    complete = afdc_json("nearest_frederick_dc_fast.json")
    complete["total_results"] = len(complete["fuel_stations"])
    afdc.nearest = paged(complete)
    body = _nearby(api, auth, limit=200).json()
    assert body["next_cursor"] is None


def test_cursor_from_another_query_is_rejected(api: TestClient, auth: Auth, afdc: FakeAfdc) -> None:
    cursor = _nearby(api, auth, limit=10).json()["next_cursor"]
    response = _nearby(api, auth, limit=10, radius_km=40, cursor=cursor)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(afdc.requests) == 1


def test_cursor_from_another_user_is_rejected(api: TestClient, auth: Auth) -> None:
    cursor = _nearby(api, auth, limit=10).json()["next_cursor"]
    response = api.get(
        STATIONS, params={**FREDERICK, "limit": 10, "cursor": cursor}, headers=auth(OTHER_USER_ID)
    )
    assert response.status_code == 422


@pytest.mark.parametrize("mangle", ["flip", "garbage", "no-signature", "empty"])
def test_tampered_cursor_is_rejected(api: TestClient, auth: Auth, mangle: str) -> None:
    cursor = _nearby(api, auth, limit=10).json()["next_cursor"]
    body, _, sig = cursor.partition(".")
    bad = {
        "flip": f"{body}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}",
        "garbage": "not-a-cursor",
        "no-signature": body,
        "empty": ".",
    }[mangle]
    response = _nearby(api, auth, limit=10, cursor=bad)
    assert response.status_code == 422


def test_without_a_secret_paging_is_off(api: TestClient, auth: Auth, settings: Any) -> None:
    settings.stations_cursor_secret = ""
    body = _nearby(api, auth, limit=10).json()
    assert body["next_cursor"] is None
    assert _nearby(api, auth, limit=10, cursor="x.y").status_code == 422


# ---------------------------------------------------------------------------
# Compatibility and ownership
# ---------------------------------------------------------------------------


def test_vehicle_connectors_are_the_default_filter(
    api: TestClient, auth: Auth, afdc: FakeAfdc, add_vehicle: Callable[..., Any]
) -> None:
    vehicle = add_vehicle(connectors=["CCS1"])
    body = _nearby(api, auth, vehicle_id=str(vehicle.id)).json()
    assert afdc.params[0]["ev_connector_type"] == "J1772COMBO"
    for item in body["items"]:
        assert item["compatible"] == any(c["type"] == "CCS1" for c in item["connectors"])
    assert any(item["compatible"] for item in body["items"])


def test_explicit_connector_overrides_the_vehicle_default(
    api: TestClient, auth: Auth, afdc: FakeAfdc, add_vehicle: Callable[..., Any]
) -> None:
    vehicle = add_vehicle(connectors=["CCS1"])
    body = _nearby(api, auth, vehicle_id=str(vehicle.id), connector="NACS").json()
    assert afdc.params[0]["ev_connector_type"] == "TESLA"
    # ``compatible`` is still about the vehicle, not the filter.
    for item in body["items"]:
        assert item["compatible"] == any(c["type"] == "CCS1" for c in item["connectors"])


def test_vehicle_without_known_connectors_adds_no_filter(
    api: TestClient, auth: Auth, afdc: FakeAfdc, add_vehicle: Callable[..., Any]
) -> None:
    vehicle = add_vehicle(connectors=[])
    body = _nearby(api, auth, vehicle_id=str(vehicle.id)).json()
    assert "ev_connector_type" not in afdc.params[0]
    assert all(item["compatible"] is None for item in body["items"])


@pytest.mark.parametrize("endpoint", ["nearby", "detail", "along"])
def test_another_users_vehicle_is_404(
    api: TestClient,
    auth: Auth,
    afdc: FakeAfdc,
    add_vehicle: Callable[..., Any],
    endpoint: str,
) -> None:
    theirs = add_vehicle(user_id=OTHER_USER_ID, connectors=["CCS1"])
    if endpoint == "nearby":
        response = _nearby(api, auth, vehicle_id=str(theirs.id))
    elif endpoint == "along":
        response = _along(api, auth, vehicle_id=str(theirs.id))
    else:
        station_id = _nearby(api, auth).json()["items"][0]["id"]
        afdc.requests.clear()
        response = api.get(
            f"{STATIONS}/{station_id}", params={"vehicle_id": str(theirs.id)}, headers=auth()
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert afdc.requests == []


# ---------------------------------------------------------------------------
# Power filter (D10)
# ---------------------------------------------------------------------------


def test_power_filter_keeps_unknown_power_stations(
    api: TestClient, auth: Auth, afdc: FakeAfdc
) -> None:
    body = afdc_json("nearby_route_all_levels_first_page.json")
    afdc.nearest = paged(body)
    stations = [s for s in map(normalize_station, body["fuel_stations"]) if not s.restricted_access]
    unknown = {s.external_id for s in stations if s.max_power_kw is None}
    fast = {s.external_id for s in stations if (s.max_power_kw or 0) >= 50}
    slow = {s.external_id for s in stations if s.max_power_kw is not None and s.max_power_kw < 50}
    assert unknown and slow  # the fixture has both kinds

    result = _nearby(api, auth, min_power_kw=50).json()
    returned = {i["external_id"] for i in result["items"]}
    assert returned == unknown | fast
    assert result["unknown_power_count"] == len(unknown)
    assert all(i["max_power_kw"] is None for i in result["items"] if i["external_id"] in unknown)
    # The filter is ours, not AFDC's (D10).
    assert "ev_power_kw_min" not in afdc.params[0]


# ---------------------------------------------------------------------------
# POST /stations/along-route (E2)
# ---------------------------------------------------------------------------


def test_cross_border_route_returns_us_and_canadian_stations_in_route_order(
    api: TestClient, auth: Auth, afdc: FakeAfdc, db: Session
) -> None:
    response = _along(api, auth)
    assert response.status_code == 200, response.text
    body = response.json()
    items = body["items"]
    assert {i["country"] for i in items} == {"US", "CA"}
    assert body["total_results"] == 122
    assert body["truncated"] is False
    assert len(items) == 122 - 3  # three restricted stations are left out
    assert _station_count(db) == len(items)

    along = [i["distance_along_route_km"] for i in items]
    assert along == sorted(along)
    # Frederick first, Toronto last: the US stations come before the Canadian ones.
    countries = [i["country"] for i in items]
    assert countries.index("CA") > len(countries) - countries[::-1].index("US") - 1
    # Fixture used a 2-mile corridor around a 0.25 km simplification.
    assert all(i["distance_from_route_km"] < 4.0 for i in items)
    assert along[-1] < 760  # route A is 748 km
    assert all(i["live_availability"] is None for i in items)


def test_along_route_ids_match_nearby_ids_for_the_same_station(
    api: TestClient, auth: Auth, afdc: FakeAfdc
) -> None:
    first = {i["external_id"]: i["id"] for i in _along(api, auth).json()["items"]}
    second = {i["external_id"]: i["id"] for i in _along(api, auth).json()["items"]}
    assert first == second


def test_along_route_sends_simplified_route_and_defaults(
    api: TestClient, auth: Auth, afdc: FakeAfdc, settings: Any
) -> None:
    settings.stations_route_max_points = 50
    response = _along(api, auth, corridor_km=3.218688)
    assert response.status_code == 200, response.text
    [params] = afdc.params
    assert params["route"].startswith("LINESTRING(")
    assert len(params["route"].split(",")) <= 50
    assert params["distance"] == "2.000"
    assert params["ev_charging_level"] == "dc_fast"
    assert params["country"] == "all"
    assert params["offset"] == "0"


def test_along_route_all_levels(api: TestClient, auth: Auth, afdc: FakeAfdc) -> None:
    _along(api, auth, charging_level="all")
    assert "ev_charging_level" not in afdc.params[0]
    afdc.requests.clear()
    _along(api, auth, charging_level="level2")
    assert afdc.params[0]["ev_charging_level"] == "2"


def test_along_route_pages_the_provider_until_done(
    api: TestClient, auth: Auth, afdc: FakeAfdc
) -> None:
    body = _along(api, auth, limit=1000).json()
    # 122 results fit in one AFDC page of 200.
    assert len(afdc.requests) == 1
    assert body["truncated"] is False

    afdc.requests.clear()
    body = _along(api, auth, limit=50).json()
    # Restricted stations don't count towards ``limit``, so a short page is
    # topped up from where the last one ended.
    pages = [(int(p["offset"]), int(p["limit"])) for p in afdc.params]
    assert pages[0] == (0, 50)
    for (offset, size), (next_offset, _) in itertools.pairwise(pages):
        assert next_offset == offset + size
    assert body["truncated"] is True
    assert len(body["items"]) == 50


def test_along_route_follows_offsets_across_pages(
    api: TestClient, auth: Auth, afdc: FakeAfdc
) -> None:
    big = afdc_json("nearby_route_cross_border_dc_fast.json")
    # Make 2 AFDC pages: 244 stations (second copy with new IDs).
    extra = copy.deepcopy(big["fuel_stations"])
    for s in extra:
        s["id"] = int(s["id"]) + 10_000_000
    big["fuel_stations"] = big["fuel_stations"] + extra
    big["total_results"] = len(big["fuel_stations"])
    afdc.nearby_route = paged(big)

    body = _along(api, auth, limit=1000).json()
    assert [(p["offset"], p["limit"]) for p in afdc.params] == [("0", "200"), ("200", "200")]
    assert body["truncated"] is False
    assert len(body["items"]) == 2 * (122 - 3)


def test_along_route_compatibility(
    api: TestClient, auth: Auth, afdc: FakeAfdc, add_vehicle: Callable[..., Any]
) -> None:
    vehicle = add_vehicle(connectors=["NACS"])
    body = _along(api, auth, vehicle_id=str(vehicle.id)).json()
    assert afdc.params[0]["ev_connector_type"] == "TESLA"
    for item in body["items"]:
        assert item["compatible"] == any(c["type"] == "NACS" for c in item["connectors"])


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"geometry_polyline6": "x" * (MAX_GEOMETRY_BYTES + 1)}, "too large"),
        ({"geometry_polyline6": "@@@"}, "truncated"),
        ({"geometry_polyline6": encode_polyline([(39.4, -77.4)])}, "one point"),
        ({"geometry_polyline6": "~~~~~~~~~~~~~~~~"}, "bad"),
        ({"geometry_polyline6": encode_polyline([(39.4, -77.4), (95.0, -77.4)])}, "range"),
        ({"corridor_km": 0.4}, "corridor"),
        ({"corridor_km": 16.1}, "corridor"),
        ({"limit": 1001}, "limit"),
        ({"connector": ["TYPE2"]}, "connector"),
        ({"unknown": 1}, "extra"),
    ],
)
def test_along_route_validation(
    api: TestClient, auth: Auth, afdc: FakeAfdc, body: dict[str, Any], reason: str
) -> None:
    response = api.post(
        ALONG, json={"geometry_polyline6": route_a_polyline(), **body}, headers=auth()
    )
    assert response.status_code == 422, reason
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert afdc.requests == []


def test_along_route_logs_no_route_or_coordinates(
    api: TestClient, auth: Auth, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    polyline = route_a_polyline()
    _along(api, auth)
    text = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert polyline[:40] not in text
    assert "LINESTRING" not in text
    lat, lon = route_a_points()[0]
    assert f"{lat:.4f}" not in text
    assert f"{lon:.4f}" not in text


# ---------------------------------------------------------------------------
# Provider failures (E6)
# ---------------------------------------------------------------------------


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    ("responder", "status", "code"),
    [
        (_timeout, 504, "STATIONS_UNREACHABLE"),
        (_refused, 502, "STATIONS_UNREACHABLE"),
        (lambda _r: httpx.Response(500, text="boom"), 502, "STATIONS_UNREACHABLE"),
        (lambda _r: httpx.Response(200, text="not json"), 502, "STATIONS_UNREACHABLE"),
        (
            lambda _r: httpx.Response(403, json=afdc_json("error_api_key_missing.json")),
            503,
            "STATIONS_NOT_CONFIGURED",
        ),
    ],
)
@pytest.mark.parametrize("endpoint", ["nearby", "along"])
def test_provider_failures_map_to_the_envelope(
    api: TestClient,
    auth: Auth,
    afdc: FakeAfdc,
    db: Session,
    responder: Callable[[httpx.Request], httpx.Response],
    status: int,
    code: str,
    endpoint: str,
) -> None:
    afdc.fail_all(responder)
    response = _nearby(api, auth) if endpoint == "nearby" else _along(api, auth)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert _station_count(db) == 0


def test_missing_api_key_is_not_configured(
    api: TestClient, auth: Auth, afdc: FakeAfdc, provider: Any
) -> None:
    provider._api_key = ""
    response = _nearby(api, auth)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STATIONS_NOT_CONFIGURED"
    assert afdc.requests == []


# ---------------------------------------------------------------------------
# GET /stations/{id} (D7: cache, TTL refresh, stale fallback)
# ---------------------------------------------------------------------------


def _cached_station(api: TestClient, auth: Auth, afdc: FakeAfdc) -> dict[str, Any]:
    """Cache the Canadian station 448148 through a search and return its item."""

    single = afdc_json("nearest_frederick_dc_fast.json")
    single["fuel_stations"] = [afdc_json("station_448148.json")["alt_fuel_station"]]
    single["total_results"] = 1
    afdc.nearest = paged(single)
    [item] = _nearby(api, auth).json()["items"]
    afdc.requests.clear()
    return item  # type: ignore[no-any-return]


def _age(db: Session, station_id: str, hours: float) -> None:
    db.execute(
        update(ChargingStation)
        .where(ChargingStation.id == uuid.UUID(station_id))
        .values(fetched_at=datetime.now(UTC) - timedelta(hours=hours))
    )
    db.commit()


def test_detail_reads_a_fresh_row_from_the_cache(
    api: TestClient, auth: Auth, afdc: FakeAfdc
) -> None:
    item = _cached_station(api, auth, afdc)
    response = api.get(f"{STATIONS}/{item['id']}", headers=auth())
    assert response.status_code == 200, response.text
    body = response.json()
    assert afdc.requests == []
    assert body["id"] == item["id"]
    assert body["country"] == "CA"
    assert body["is_stale"] is False
    assert body["live_availability"] is None
    assert body["connectors"] == item["connectors"]
    assert body["restricted_access"] is False


def test_detail_refreshes_after_the_ttl(
    api: TestClient, auth: Auth, afdc: FakeAfdc, db: Session
) -> None:
    item = _cached_station(api, auth, afdc)
    _age(db, item["id"], hours=25)

    changed = afdc_json("station_448148.json")
    changed["alt_fuel_station"]["station_name"] = "Refreshed Name"
    afdc.station = lambda _r: httpx.Response(200, json=changed)

    body = api.get(f"{STATIONS}/{item['id']}", headers=auth()).json()
    [request] = afdc.requests
    assert request.url.path.endswith("/448148.json")
    assert body["id"] == item["id"]
    assert body["name"] == "Refreshed Name"
    assert body["is_stale"] is False
    fetched = datetime.fromisoformat(body["fetched_at"].replace("Z", "+00:00"))
    assert datetime.now(UTC) - fetched < timedelta(minutes=5)


@pytest.mark.parametrize(
    "responder",
    [
        _timeout,
        lambda _r: httpx.Response(500, text="boom"),
        lambda _r: httpx.Response(403, json={}),
        lambda _r: httpx.Response(404, json=afdc_json("station_not_found.json")),
    ],
)
def test_failed_refresh_returns_the_stale_cached_row(
    api: TestClient,
    auth: Auth,
    afdc: FakeAfdc,
    db: Session,
    responder: Callable[[httpx.Request], httpx.Response],
) -> None:
    item = _cached_station(api, auth, afdc)
    _age(db, item["id"], hours=25)
    afdc.station = responder

    response = api.get(f"{STATIONS}/{item['id']}", headers=auth())
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(afdc.requests) == 1
    assert body["is_stale"] is True
    assert body["name"] == item["name"]
    assert body["connectors"] == item["connectors"]


def test_unknown_station_is_404(api: TestClient, auth: Auth, afdc: FakeAfdc) -> None:
    response = api.get(f"{STATIONS}/{uuid.uuid4()}", headers=auth())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert afdc.requests == []


def test_non_uuid_station_id_is_422(api: TestClient, auth: Auth) -> None:
    assert api.get(f"{STATIONS}/448148", headers=auth()).status_code == 422


def test_detail_marks_compatibility_for_the_callers_vehicle(
    api: TestClient, auth: Auth, afdc: FakeAfdc, add_vehicle: Callable[..., Any]
) -> None:
    item = _cached_station(api, auth, afdc)
    types = {c["type"] for c in item["connectors"]}
    vehicle = add_vehicle(user_id=TEST_USER_ID, connectors=sorted(types)[:1])
    body = api.get(
        f"{STATIONS}/{item['id']}", params={"vehicle_id": str(vehicle.id)}, headers=auth()
    ).json()
    assert body["compatible"] is True
