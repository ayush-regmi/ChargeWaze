"""Tests for the places and directions endpoints (Phase 3 F4).

``GET /api/v1/places/suggest``, ``GET /api/v1/places/{id}`` and
``POST /api/v1/routes/directions``. Mapbox is a real ``MapboxClient`` over
``httpx.MockTransport`` serving the F0 fixtures, so requests go through the real
adapter. None of these endpoints touch the database, so the app here has no DB.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.integrations.mapbox.client import MapboxClient, get_mapbox_client
from app.main import create_app
from tests.conftest import _KeyMaterial, _StubVerifier, make_token

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "mapbox"
SUGGEST = "/api/v1/places/suggest"
PLACES = "/api/v1/places"
DIRECTIONS = "/api/v1/routes/directions"
SESSION = "6f1c2b9e-1d1e-4a3b-9b1e-0c6a1f2d3e4f"
TOKEN = "sk.test-secret-token"
FREDERICK = {"lat": 39.4143, "lon": -77.4105}
TORONTO = {"lat": 43.6532, "lon": -79.3832}
TORONTO_ID = "dXJuOm1ieHBsYzpCK3dJSnc"


def fixture_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_bytes())


class FakeMapbox:
    """Routes each Mapbox path to a scripted response and records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.directions: Callable[[], httpx.Response] = lambda: httpx.Response(
            200, json=fixture_json("directions_frederick_toronto.json")
        )
        self.suggest: Callable[[], httpx.Response] = lambda: httpx.Response(
            200, json=fixture_json("suggest_toronto.json")
        )
        self.retrieve: Callable[[], httpx.Response] = lambda: httpx.Response(
            200, json=fixture_json("retrieve_toronto.json")
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/directions/"):
            return self.directions()
        if path.endswith("/suggest"):
            return self.suggest()
        if "/retrieve/" in path:
            return self.retrieve()
        return httpx.Response(404, json={"message": "Not Found"})

    @property
    def only(self) -> httpx.Request:
        [request] = self.requests
        return request

    def query(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(str(self.only.url)).query)


@pytest.fixture()
def mapbox() -> FakeMapbox:
    return FakeMapbox()


@pytest.fixture()
def app(key_material: _KeyMaterial, mapbox: FakeMapbox) -> Iterator[FastAPI]:
    client = MapboxClient(
        access_token=TOKEN,
        api_base_url="https://mapbox.test",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(mapbox),
    )
    app = create_app()
    app.state.jwt_verifier = _StubVerifier(key_material)
    app.dependency_overrides[get_mapbox_client] = lambda: client
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        client.close()


@pytest.fixture()
def api(app: FastAPI, key_material: _KeyMaterial) -> TestClient:
    test_client = TestClient(app)
    test_client.headers["Authorization"] = f"Bearer {make_token(key_material)}"
    return test_client


@pytest.fixture()
def anonymous(app: FastAPI) -> TestClient:
    return TestClient(app)


def error_code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


# ---- auth ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        ("GET", SUGGEST, {"params": {"q": "Toronto", "session_token": SESSION}}),
        ("GET", f"{PLACES}/{TORONTO_ID}", {"params": {"session_token": SESSION}}),
        ("POST", DIRECTIONS, {"json": {"origin": FREDERICK, "destination": TORONTO}}),
    ],
)
def test_endpoints_require_auth(
    anonymous: TestClient, mapbox: FakeMapbox, method: str, url: str, kwargs: dict[str, Any]
) -> None:
    response = anonymous.request(method, url, **kwargs)
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"
    assert mapbox.requests == []


# ---- GET /places/suggest ------------------------------------------------------


def test_suggest_returns_items_and_passes_proximity(api: TestClient, mapbox: FakeMapbox) -> None:
    response = api.get(
        SUGGEST,
        params={"q": "  Toronto ", "session_token": SESSION, "lat": 43.65, "lon": -79.38},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "fixture has suggestions"
    first = items[0]
    assert first == {
        "id": TORONTO_ID,
        "name": "Toronto",
        "full_address": "Toronto, Ontario, Canada",
        "kind": "place",
        "country_code": "CA",
    }
    assert {item["kind"] for item in items} <= {"address", "poi", "place"}
    for item in items:
        assert "lat" not in item and "location" not in item

    q = mapbox.query()
    assert q["q"] == ["Toronto"]  # trimmed
    assert q["session_token"] == [SESSION]
    assert q["proximity"] == ["-79.380000,43.650000"]
    assert q["limit"] == ["5"]


def test_suggest_without_coordinates_falls_back_to_ip(api: TestClient, mapbox: FakeMapbox) -> None:
    response = api.get(SUGGEST, params={"q": "Toronto", "session_token": SESSION, "limit": 10})
    assert response.status_code == 200, response.text
    assert mapbox.query()["proximity"] == ["ip"]
    assert mapbox.query()["limit"] == ["10"]


def test_suggest_no_matches_is_empty_list(api: TestClient, mapbox: FakeMapbox) -> None:
    mapbox.suggest = lambda: httpx.Response(200, json={"suggestions": [], "attribution": "x"})
    response = api.get(SUGGEST, params={"q": "zzzzqqq", "session_token": SESSION})
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.parametrize(
    "params",
    [
        {"q": "To", "session_token": SESSION},
        {"q": "   To   ", "session_token": SESSION},
        {"q": "x" * 201, "session_token": SESSION},
        {"session_token": SESSION},
        {"q": "Toronto"},
        {"q": "Toronto", "session_token": "not-a-uuid"},
        {"q": "Toronto", "session_token": SESSION, "lat": 91, "lon": 0},
        {"q": "Toronto", "session_token": SESSION, "lat": 0, "lon": -181},
        {"q": "Toronto", "session_token": SESSION, "lat": "nan", "lon": 0},
        {"q": "Toronto", "session_token": SESSION, "lat": 43.65},
        {"q": "Toronto", "session_token": SESSION, "lon": -79.38},
        {"q": "Toronto", "session_token": SESSION, "limit": 0},
        {"q": "Toronto", "session_token": SESSION, "limit": 11},
    ],
    ids=[
        "q-too-short",
        "q-short-after-trim",
        "q-too-long",
        "q-missing",
        "session-missing",
        "session-not-uuid",
        "lat-out-of-range",
        "lon-out-of-range",
        "lat-nan",
        "lat-without-lon",
        "lon-without-lat",
        "limit-zero",
        "limit-too-big",
    ],
)
def test_suggest_validation(api: TestClient, mapbox: FakeMapbox, params: dict[str, Any]) -> None:
    response = api.get(SUGGEST, params=params)
    assert response.status_code == 422, response.text
    assert error_code(response) == "VALIDATION_ERROR"
    assert mapbox.requests == []


# ---- GET /places/{id} ---------------------------------------------------------


def test_get_place_returns_location(api: TestClient, mapbox: FakeMapbox) -> None:
    response = api.get(f"{PLACES}/{TORONTO_ID}", params={"session_token": SESSION})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == TORONTO_ID
    assert body["name"] == "Toronto"
    assert body["kind"] == "place"
    assert body["country_code"] == "CA"
    assert body["location"] == {"lat": pytest.approx(43.647938), "lon": pytest.approx(-79.38355)}
    assert mapbox.only.url.path.endswith(f"/retrieve/{TORONTO_ID}")
    assert mapbox.query()["session_token"] == [SESSION]


def test_get_place_poi(api: TestClient, mapbox: FakeMapbox) -> None:
    mapbox.retrieve = lambda: httpx.Response(
        200, json=fixture_json("retrieve_tesla_supercharger_frederick.json")
    )
    response = api.get(f"{PLACES}/some-poi-id", params={"session_token": SESSION})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "poi"
    assert body["country_code"] == "US"


@pytest.mark.parametrize(
    "upstream",
    [
        lambda: httpx.Response(404, json={"message": "Not Found"}),
        lambda: httpx.Response(200, json={"type": "FeatureCollection", "features": []}),
    ],
    ids=["mapbox-404", "empty-features"],
)
def test_get_place_unknown_id_is_404(
    api: TestClient, mapbox: FakeMapbox, upstream: Callable[[], httpx.Response]
) -> None:
    mapbox.retrieve = upstream
    response = api.get(f"{PLACES}/unknown", params={"session_token": SESSION})
    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


@pytest.mark.parametrize(
    ("place_id", "params"),
    [
        (TORONTO_ID, {}),
        (TORONTO_ID, {"session_token": "nope"}),
        ("x" * 513, {"session_token": SESSION}),
    ],
    ids=["session-missing", "session-not-uuid", "id-too-long"],
)
def test_get_place_validation(
    api: TestClient, mapbox: FakeMapbox, place_id: str, params: dict[str, str]
) -> None:
    response = api.get(f"{PLACES}/{place_id}", params=params)
    assert response.status_code == 422, response.text
    assert error_code(response) == "VALIDATION_ERROR"
    assert mapbox.requests == []


# ---- POST /routes/directions --------------------------------------------------


def test_directions_returns_alternatives(api: TestClient, mapbox: FakeMapbox) -> None:
    response = api.post(DIRECTIONS, json={"origin": FREDERICK, "destination": TORONTO})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["no_route_reason"] is None
    routes = body["routes"]
    assert 1 <= len(routes) <= 3
    assert [r["index"] for r in routes] == list(range(len(routes)))
    first = routes[0]
    assert first["distance_m"] == pytest.approx(748107.875)
    assert first["duration_s"] == pytest.approx(30890.682)
    assert first["summary"] == "US 15, QEW"
    assert first["countries"] == ["US", "CA"]
    assert first["geometry_polyline6"].startswith("iwsdjAdlwsrC")

    # Mapbox URLs are lon,lat.
    assert "/-77.410500,39.414300;-79.383200,43.653200" in mapbox.only.url.path


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("directions_no_route.json", "no_route"),
        ("directions_no_segment.json", "no_road_near_point"),
    ],
)
def test_directions_no_route_is_200(
    api: TestClient, mapbox: FakeMapbox, fixture: str, reason: str
) -> None:
    mapbox.directions = lambda: httpx.Response(200, json=fixture_json(fixture))
    response = api.post(DIRECTIONS, json={"origin": FREDERICK, "destination": TORONTO})
    assert response.status_code == 200, response.text
    assert response.json() == {"routes": [], "no_route_reason": reason}


@pytest.mark.parametrize(
    "body",
    [
        {"origin": FREDERICK},
        {"destination": TORONTO},
        {"origin": {"lat": 91, "lon": 0}, "destination": TORONTO},
        {"origin": FREDERICK, "destination": {"lat": 0, "lon": 180.5}},
        {"origin": {"lat": 39.4}, "destination": TORONTO},
        {"origin": {"lat": "abc", "lon": 0}, "destination": TORONTO},
        {"origin": FREDERICK, "destination": FREDERICK},
        # About 30 m apart.
        {"origin": FREDERICK, "destination": {"lat": 39.41457, "lon": -77.4105}},
        {"origin": FREDERICK, "destination": TORONTO, "waypoints": []},
    ],
    ids=[
        "destination-missing",
        "origin-missing",
        "lat-out-of-range",
        "lon-out-of-range",
        "lon-missing",
        "lat-not-number",
        "identical-points",
        "within-50m",
        "unknown-field",
    ],
)
def test_directions_validation(api: TestClient, mapbox: FakeMapbox, body: dict[str, Any]) -> None:
    response = api.post(DIRECTIONS, json=body)
    assert response.status_code == 422, response.text
    assert error_code(response) == "VALIDATION_ERROR"
    assert mapbox.requests == []


def test_directions_points_just_over_50m_apart_are_accepted(
    api: TestClient, mapbox: FakeMapbox
) -> None:
    # About 60 m north.
    response = api.post(
        DIRECTIONS, json={"origin": FREDERICK, "destination": {"lat": 39.41484, "lon": -77.4105}}
    )
    assert response.status_code == 200, response.text
    assert len(mapbox.requests) == 1


# ---- upstream errors ----------------------------------------------------------


def _timeout() -> httpx.Response:
    raise httpx.ReadTimeout("timed out")


def _connect_error() -> httpx.Response:
    raise httpx.ConnectError("refused")


UPSTREAM_ERRORS = [
    (lambda: httpx.Response(401, json=fixture_json("error_invalid_token.json")), 503,
     "MAPBOX_NOT_CONFIGURED"),
    (lambda: httpx.Response(403, json={"message": "Forbidden"}), 503, "MAPBOX_NOT_CONFIGURED"),
    (_timeout, 504, "MAPBOX_UNREACHABLE"),
    (_connect_error, 502, "MAPBOX_UNREACHABLE"),
    (lambda: httpx.Response(503, text="down"), 502, "MAPBOX_UNREACHABLE"),
    (lambda: httpx.Response(422, json={"code": "InvalidInput"}), 502, "MAPBOX_ERROR"),
    (lambda: httpx.Response(200, text="not json"), 502, "MAPBOX_ERROR"),
]
UPSTREAM_IDS = ["401", "403", "timeout", "connect", "5xx", "4xx", "malformed"]


@pytest.mark.parametrize(("upstream", "status", "code"), UPSTREAM_ERRORS, ids=UPSTREAM_IDS)
def test_suggest_upstream_errors(
    api: TestClient,
    mapbox: FakeMapbox,
    upstream: Callable[[], httpx.Response],
    status: int,
    code: str,
) -> None:
    mapbox.suggest = upstream
    response = api.get(SUGGEST, params={"q": "Toronto", "session_token": SESSION})
    assert response.status_code == status, response.text
    assert error_code(response) == code


@pytest.mark.parametrize(("upstream", "status", "code"), UPSTREAM_ERRORS, ids=UPSTREAM_IDS)
def test_get_place_upstream_errors(
    api: TestClient,
    mapbox: FakeMapbox,
    upstream: Callable[[], httpx.Response],
    status: int,
    code: str,
) -> None:
    mapbox.retrieve = upstream
    response = api.get(f"{PLACES}/{TORONTO_ID}", params={"session_token": SESSION})
    assert response.status_code == status, response.text
    assert error_code(response) == code


@pytest.mark.parametrize(("upstream", "status", "code"), UPSTREAM_ERRORS, ids=UPSTREAM_IDS)
def test_directions_upstream_errors(
    api: TestClient,
    mapbox: FakeMapbox,
    upstream: Callable[[], httpx.Response],
    status: int,
    code: str,
) -> None:
    mapbox.directions = upstream
    response = api.post(DIRECTIONS, json={"origin": FREDERICK, "destination": TORONTO})
    assert response.status_code == status, response.text
    assert error_code(response) == code


def test_missing_token_is_not_configured(key_material: _KeyMaterial) -> None:
    unconfigured = MapboxClient(
        access_token="", api_base_url="https://mapbox.test", timeout_seconds=5.0
    )
    app = create_app()
    app.state.jwt_verifier = _StubVerifier(key_material)
    app.dependency_overrides[get_mapbox_client] = lambda: unconfigured
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {make_token(key_material)}"

    response = client.post(DIRECTIONS, json={"origin": FREDERICK, "destination": TORONTO})
    assert response.status_code == 503
    assert error_code(response) == "MAPBOX_NOT_CONFIGURED"


# ---- logging (D13) ------------------------------------------------------------


def test_logs_have_no_search_text_ids_tokens_or_coordinates(
    api: TestClient, mapbox: FakeMapbox, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    api.get(
        SUGGEST,
        params={"q": "Secret Street 42", "session_token": SESSION, "lat": 43.6532, "lon": -79.3832},
    )
    api.get(f"{PLACES}/{TORONTO_ID}", params={"session_token": SESSION})
    api.post(DIRECTIONS, json={"origin": FREDERICK, "destination": TORONTO})
    mapbox.directions = _timeout
    api.post(DIRECTIONS, json={"origin": FREDERICK, "destination": TORONTO})

    assert len(mapbox.requests) == 4
    # The TestClient logs its own request URLs (logger ``httpx*``); that's the
    # test's HTTP client, not the server, so only server-side records count.
    logged = "\n".join(
        f"{record.getMessage()} {record.__dict__}"
        for record in caplog.records
        if not record.name.startswith("httpx")
    )
    assert "mapbox_request" in logged
    for secret in (
        "Secret Street",
        "Secret+Street",
        SESSION,
        TOKEN,
        TORONTO_ID,
        "43.6532",
        "79.3832",
        "39.4143",
        "77.4105",
        "iwsdjAdlwsrC",
    ):
        assert secret not in logged, secret


def test_uvicorn_access_log_drops_query_string() -> None:
    create_app()
    access = logging.getLogger("uvicorn.access")
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/v1/places/suggest?q=Secret+Street&lat=43.6532", "1.1", 200),
        None,
    )
    assert access.filter(record)
    message = record.getMessage()
    assert "/api/v1/places/suggest HTTP/1.1" in message
    assert "Secret" not in message and "43.6532" not in message
