"""Mapbox adapter tests. No network: every client uses ``httpx.MockTransport``
serving the F0 fixtures in ``fixtures/mapbox/``."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.geo import decode_polyline
from app.integrations.mapbox.client import MapboxClient

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "mapbox"
APP_DIR = Path(__file__).resolve().parents[2] / "app"
API_BASE = "https://mapbox.test"
TOKEN = "sk.test-secret-token"
SESSION = "6f1c2b9e-1d1e-4a3b-9b1e-0c6a1f2d3e4f"
FREDERICK = (39.4143, -77.4105)
TORONTO = (43.6532, -79.3832)

Handler = Callable[[httpx.Request], httpx.Response]


def fixture_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_bytes())


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


def serve(name: str, status_code: int = 200) -> Recorder:
    return Recorder(httpx.Response(status_code, json=fixture_json(name)))


def make_client(handler: Handler, **overrides: Any) -> MapboxClient:
    kwargs: dict[str, Any] = {
        "access_token": TOKEN,
        "api_base_url": API_BASE,
        "timeout_seconds": 5.0,
        "transport": httpx.MockTransport(handler),
    }
    kwargs.update(overrides)
    return MapboxClient(**kwargs)


def query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(str(request.url)).query)


# ---- config -------------------------------------------------------------------


def test_from_settings_reads_mapbox_settings() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        mapbox_secret_token=TOKEN,
        mapbox_api_base_url=f"{API_BASE}/",
    )
    recorder = serve("directions_frederick_toronto.json")
    client = MapboxClient.from_settings(settings, transport=httpx.MockTransport(recorder))
    client.directions(FREDERICK, TORONTO)
    assert str(recorder.only.url).startswith(f"{API_BASE}/directions/")


def test_default_settings_point_at_mapbox() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.mapbox_api_base_url == "https://api.mapbox.com"
    assert settings.mapbox_secret_token == ""


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.directions(FREDERICK, TORONTO),
        lambda c: c.suggest("Toronto", session_token=SESSION),
        lambda c: c.retrieve("abc", session_token=SESSION),
    ],
)
def test_empty_token_is_not_configured_and_sends_nothing(
    call: Callable[[MapboxClient], Any],
) -> None:
    recorder = serve("directions_frederick_toronto.json")
    client = make_client(recorder, access_token="")
    with pytest.raises(ApiError) as exc:
        call(client)
    assert exc.value.code == "MAPBOX_NOT_CONFIGURED"
    assert exc.value.status_code == 503
    assert recorder.requests == []


# ---- directions: request --------------------------------------------------------


def test_directions_request_shape() -> None:
    recorder = serve("directions_frederick_toronto.json")
    make_client(recorder).directions(FREDERICK, TORONTO)

    request = recorder.only
    assert request.method == "GET"
    assert unquote(request.url.path) == (
        "/directions/v5/mapbox/driving/-77.410500,39.414300;-79.383200,43.653200"
    )
    assert query(request) == {
        "alternatives": ["true"],
        "geometries": ["polyline6"],
        "overview": ["full"],
        "steps": ["false"],
        "access_token": [TOKEN],
    }
    # The token only travels in the query string, never in a header.
    assert all(TOKEN not in value for value in request.headers.values())


def test_directions_rejects_out_of_range_coordinates() -> None:
    recorder = serve("directions_frederick_toronto.json")
    with pytest.raises(ValueError):
        make_client(recorder).directions((91.0, 0.0), TORONTO)
    assert recorder.requests == []


# ---- directions: parsing --------------------------------------------------------


def test_directions_parses_alternatives() -> None:
    result = make_client(serve("directions_frederick_toronto.json")).directions(FREDERICK, TORONTO)

    assert result.no_route_reason is None
    assert len(result.routes) == 2
    a, b = result.routes
    assert a.distance_m == pytest.approx(748_107.875)
    assert a.duration_s == pytest.approx(30_890.682)
    assert a.summary == "US 15, QEW"
    assert a.countries == ["US", "CA"]
    assert b.summary == "US 15, I 99 North"
    assert b.distance_m == pytest.approx(804_999)
    # Geometry is passed through untouched and decodes to the full route.
    raw = fixture_json("directions_frederick_toronto.json")["routes"][0]["geometry"]
    assert a.geometry_polyline6 == raw
    assert len(decode_polyline(a.geometry_polyline6)) == 9719


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("directions_no_route.json", "no_route"),
        ("directions_no_segment.json", "no_road_near_point"),
    ],
)
def test_directions_no_route_is_a_result(fixture: str, reason: str) -> None:
    result = make_client(serve(fixture)).directions(FREDERICK, TORONTO)
    assert result.routes == []
    assert result.no_route_reason == reason


def test_directions_unknown_ok_code_is_upstream_error() -> None:
    recorder = Recorder(httpx.Response(200, json={"code": "Surprise", "routes": []}))
    with pytest.raises(ApiError) as exc:
        make_client(recorder).directions(FREDERICK, TORONTO)
    assert exc.value.code == "MAPBOX_ERROR"
    assert exc.value.status_code == 502
    assert exc.value.details == {"upstream_code": "Surprise"}


@pytest.mark.parametrize("body", [b"not json", b'{"routes": "nope"}', b"[]"])
def test_directions_malformed_body_is_upstream_error(body: bytes) -> None:
    recorder = Recorder(httpx.Response(200, content=body))
    with pytest.raises(ApiError) as exc:
        make_client(recorder).directions(FREDERICK, TORONTO)
    assert exc.value.code == "MAPBOX_ERROR"


def test_directions_ok_without_routes_is_upstream_error() -> None:
    recorder = Recorder(httpx.Response(200, json={"code": "Ok"}))
    with pytest.raises(ApiError) as exc:
        make_client(recorder).directions(FREDERICK, TORONTO)
    assert exc.value.code == "MAPBOX_ERROR"


def test_directions_ok_with_empty_routes_is_no_route() -> None:
    recorder = Recorder(httpx.Response(200, json={"code": "Ok", "routes": []}))
    result = make_client(recorder).directions(FREDERICK, TORONTO)
    assert result.routes == []
    assert result.no_route_reason == "no_route"


# ---- search box -----------------------------------------------------------------


def test_suggest_request_shape() -> None:
    recorder = serve("suggest_toronto.json")
    make_client(recorder).suggest(
        "Toronto",
        session_token=SESSION,
        proximity=(43.65, -79.38),
        countries=["US", "CA"],
        limit=5,
    )
    request = recorder.only
    assert request.url.path == "/search/searchbox/v1/suggest"
    assert query(request) == {
        "q": ["Toronto"],
        "session_token": [SESSION],
        "proximity": ["-79.380000,43.650000"],
        "country": ["us,ca"],
        "limit": ["5"],
        "language": ["en"],
        "access_token": [TOKEN],
    }


def test_suggest_without_proximity_falls_back_to_ip() -> None:
    recorder = serve("suggest_toronto.json")
    make_client(recorder).suggest("Toronto", session_token=SESSION)
    params = query(recorder.only)
    assert params["proximity"] == ["ip"]
    assert "country" not in params


@pytest.mark.parametrize("limit", [0, 11])
def test_suggest_rejects_bad_limit(limit: int) -> None:
    recorder = serve("suggest_toronto.json")
    with pytest.raises(ValueError):
        make_client(recorder).suggest("Toronto", session_token=SESSION, limit=limit)
    assert recorder.requests == []


def test_suggest_parses_places_and_pois() -> None:
    suggestions = make_client(serve("suggest_toronto.json")).suggest(
        "Toronto", session_token=SESSION
    )
    assert len(suggestions) == 5
    city = suggestions[0]
    assert city.kind == "place"
    assert city.name == "Toronto"
    assert city.full_address == "Toronto, Ontario, Canada"  # built from place_formatted
    assert city.country_code == "CA"
    assert city.id == "dXJuOm1ieHBsYzpCK3dJSnc"

    airport = suggestions[4]
    assert airport.kind == "poi"
    assert airport.full_address == "6301 Silver Dart Drive, Mississauga, L5P 1B2, Canada"
    assert {s.kind for s in suggestions} == {"place", "poi"}


def test_suggest_parses_poi_addresses() -> None:
    suggestions = make_client(serve("suggest_tesla_supercharger_frederick.json")).suggest(
        "Tesla Supercharger Frederick", session_token=SESSION
    )
    assert len(suggestions) == 5
    assert all(s.kind == "poi" and s.country_code == "US" for s in suggestions)
    assert suggestions[0].full_address == (
        "5500 Buckeystown Pike, Frederick, Maryland 21704, United States"
    )


def test_suggest_maps_address_feature_type() -> None:
    body = {
        "suggestions": [
            {
                "mapbox_id": "addr-1",
                "name": "100 Main St",
                "feature_type": "address",
                "full_address": "100 Main St, Frederick, Maryland 21701, United States",
            }
        ]
    }
    [s] = make_client(Recorder(httpx.Response(200, json=body))).suggest(
        "100 Main", session_token=SESSION
    )
    assert s.kind == "address"
    assert s.country_code is None


def test_retrieve_request_and_parse() -> None:
    recorder = serve("retrieve_toronto.json")
    place = make_client(recorder).retrieve("dXJuOm1ieHBsYzpCK3dJSnc", session_token=SESSION)

    request = recorder.only
    assert request.url.path == "/search/searchbox/v1/retrieve/dXJuOm1ieHBsYzpCK3dJSnc"
    assert query(request) == {"session_token": [SESSION], "access_token": [TOKEN]}

    assert place is not None
    assert place.id == "dXJuOm1ieHBsYzpCK3dJSnc"
    assert place.kind == "place"
    assert place.full_address == "Toronto, Ontario, Canada"
    assert (place.lat, place.lon) == (43.647938, -79.38355)
    assert place.country_code == "CA"


def test_retrieve_poi() -> None:
    place = make_client(serve("retrieve_tesla_supercharger_frederick.json")).retrieve(
        "poi-id", session_token=SESSION
    )
    assert place is not None
    assert place.kind == "poi"
    assert place.full_address.startswith("5500 Buckeystown Pike")
    assert place.lat == pytest.approx(39.38465459)
    assert place.lon == pytest.approx(-77.40551751)


def test_retrieve_escapes_id_in_path() -> None:
    recorder = serve("retrieve_toronto.json")
    make_client(recorder).retrieve("a/b?c", session_token=SESSION)
    assert recorder.only.url.raw_path.startswith(b"/search/searchbox/v1/retrieve/a%2Fb%3Fc?")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"type": "FeatureCollection", "features": []}),
        httpx.Response(404, json={"message": "Not Found"}),
    ],
)
def test_retrieve_unknown_id_is_none(response: httpx.Response) -> None:
    assert make_client(Recorder(response)).retrieve("gone", session_token=SESSION) is None


@pytest.mark.parametrize("body", [{}, {"suggestions": None}, {"attribution": "x"}])
def test_suggest_malformed_body_is_upstream_error(body: dict[str, Any]) -> None:
    with pytest.raises(ApiError) as exc:
        make_client(Recorder(httpx.Response(200, json=body))).suggest(
            "Toronto", session_token=SESSION
        )
    assert exc.value.code == "MAPBOX_ERROR"
    assert exc.value.status_code == 502


def test_suggest_explicitly_empty_is_no_matches() -> None:
    recorder = Recorder(httpx.Response(200, json={"suggestions": []}))
    assert make_client(recorder).suggest("zzqx", session_token=SESSION) == []


@pytest.mark.parametrize("body", [{}, {"type": "FeatureCollection"}, {"features": None}])
def test_retrieve_malformed_body_is_upstream_error(body: dict[str, Any]) -> None:
    with pytest.raises(ApiError) as exc:
        make_client(Recorder(httpx.Response(200, json=body))).retrieve("abc", session_token=SESSION)
    assert exc.value.code == "MAPBOX_ERROR"
    assert exc.value.status_code == 502


# ---- error mapping --------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "status_code"),
    [
        (httpx.ReadTimeout("slow"), 504),
        (httpx.ConnectTimeout("slow"), 504),
        (httpx.ConnectError("refused"), 502),
    ],
)
def test_transport_errors(exc: httpx.TransportError, status_code: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(ApiError) as err:
        make_client(handler).directions(FREDERICK, TORONTO)
    assert err.value.code == "MAPBOX_UNREACHABLE"
    assert err.value.status_code == status_code


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_5xx_is_unreachable(status_code: int) -> None:
    recorder = Recorder(httpx.Response(status_code, text="upstream down"))
    with pytest.raises(ApiError) as err:
        make_client(recorder).suggest("Toronto", session_token=SESSION)
    assert err.value.code == "MAPBOX_UNREACHABLE"
    assert err.value.status_code == 502
    assert err.value.details == {"upstream_status": status_code}


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failure_is_not_configured(status_code: int) -> None:
    recorder = Recorder(httpx.Response(status_code, json=fixture_json("error_invalid_token.json")))
    with pytest.raises(ApiError) as err:
        make_client(recorder).directions(FREDERICK, TORONTO)
    assert err.value.code == "MAPBOX_NOT_CONFIGURED"
    assert err.value.status_code == 503


def test_other_4xx_is_upstream_error_without_message() -> None:
    recorder = Recorder(httpx.Response(400, json=fixture_json("error_session_token_missing.json")))
    with pytest.raises(ApiError) as err:
        make_client(recorder).suggest("Toronto", session_token=SESSION)
    assert err.value.code == "MAPBOX_ERROR"
    assert err.value.status_code == 502
    # Search Box's opaque ``message.code`` isn't passed on, nor its message.
    assert err.value.details == {"upstream_status": 400}


def test_directions_422_passes_upstream_code() -> None:
    body = {"code": "InvalidInput", "message": "Coordinate is invalid: 200,0"}
    with pytest.raises(ApiError) as err:
        make_client(Recorder(httpx.Response(422, json=body))).directions(FREDERICK, TORONTO)
    assert err.value.code == "MAPBOX_ERROR"
    assert err.value.details == {"upstream_status": 422, "upstream_code": "InvalidInput"}
    assert "200,0" not in err.value.message


# ---- logging (D13) --------------------------------------------------------------


def test_logs_have_no_secrets_queries_ids_or_coordinates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.integrations.mapbox")
    client = make_client(
        lambda request: httpx.Response(
            200,
            json=fixture_json(
                "directions_frederick_toronto.json"
                if "directions" in request.url.path
                else "retrieve_toronto.json"
                if "retrieve" in request.url.path
                else "suggest_toronto.json"
            ),
        )
    )
    client.directions(FREDERICK, TORONTO)
    client.suggest("Secret Street 42", session_token=SESSION, proximity=(43.6532, -79.3832))
    client.retrieve("dXJuOm1ieHBsYzpCK3dJSnc", session_token=SESSION)

    records = [r for r in caplog.records if r.name.startswith("app.integrations.mapbox")]
    assert [r.getMessage() for r in records] == ["mapbox_request"] * 3
    assert [r.__dict__["endpoint"] for r in records] == [
        "directions/v5/mapbox/driving",
        "search/searchbox/v1/suggest",
        "search/searchbox/v1/retrieve/{id}",
    ]
    for record in records:
        assert record.__dict__["provider"] == "mapbox"
        assert record.__dict__["status"] == 200

    # Everything we put on the records: the message plus our ``extra`` fields.
    standard = set(vars(logging.LogRecord("x", 0, "", 0, "", None, None)))
    dumped = " ".join(
        json.dumps(
            {"msg": r.getMessage(), **{k: v for k, v in vars(r).items() if k not in standard}},
            default=str,
        )
        for r in caplog.records
    )
    for forbidden in (TOKEN, SESSION, "Secret Street", "dXJuOm1ieHBsYzpCK3dJSnc", "-77.41"):
        assert forbidden not in dumped
    # No coordinate-like numbers with more than 2 decimals (durations are rounded to 1).
    assert not re.search(r"\d\.\d{3,}", dumped)


def test_failed_request_is_logged_without_status(caplog: pytest.LogCaptureFixture) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    caplog.set_level(logging.INFO, logger="app.integrations.mapbox")
    with pytest.raises(ApiError):
        make_client(handler).directions(FREDERICK, TORONTO)
    [record] = [r for r in caplog.records if r.getMessage() == "mapbox_request"]
    assert record.__dict__["status"] is None


# ---- boundary -----------------------------------------------------------------


def test_only_the_adapter_talks_to_mapbox() -> None:
    """Nothing outside ``integrations/mapbox`` (and the settings default) names Mapbox's API."""

    pattern = re.compile(r"api\.mapbox\.com|/directions/v5|searchbox/v1")
    allowed = {APP_DIR / "core" / "config.py"}
    offenders = [
        path.relative_to(APP_DIR)
        for path in APP_DIR.rglob("*.py")
        if "integrations/mapbox" not in path.as_posix()
        and path not in allowed
        and pattern.search(path.read_text())
    ]
    assert offenders == []
