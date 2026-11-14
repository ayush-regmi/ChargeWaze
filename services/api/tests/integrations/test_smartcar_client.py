from __future__ import annotations

import json
import logging
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.errors import ApiError
from app.integrations.smartcar.client import SmartcarClient
from tests.integrations.conftest import (
    API_BASE,
    AUTH_URL,
    CONNECT_URL,
    FakeClock,
    fixture_json,
)

MakeClient = Callable[..., SmartcarClient]


class Recorder:
    """MockTransport handler that serves the token endpoint plus scripted API routes."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self.routes: dict[tuple[str, str], list[httpx.Response]] = {}

    def add(self, method: str, path: str, *responses: httpx.Response) -> None:
        self.routes.setdefault((method, path), []).extend(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == AUTH_URL:
            self.token_calls += 1
            body = dict(fixture_json("token_response.json"))
            body["access_token"] = f"token-{self.token_calls}"
            return httpx.Response(200, json=body)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"errors": [{"code": "NOT_FOUND"}]})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url) != AUTH_URL]


@pytest.fixture()
def recorder() -> Recorder:
    return Recorder()


# ---- token ------------------------------------------------------------------


def test_token_request_uses_client_credentials(make_client: MakeClient, recorder: Recorder) -> None:
    client = make_client(recorder)
    assert client.get_application_token() == "token-1"
    [request] = recorder.requests
    assert request.method == "POST"
    form = parse_qs(request.content.decode())
    assert form == {
        "grant_type": ["client_credentials"],
        "client_id": ["test-client-id"],
        "client_secret": ["test-client-secret"],
    }


def test_token_is_cached_until_near_expiry(
    make_client: MakeClient, recorder: Recorder, clock: FakeClock
) -> None:
    client = make_client(recorder)
    assert client.get_application_token() == "token-1"
    clock.now += 3600 - 61  # still inside the 60 s refresh margin
    assert client.get_application_token() == "token-1"
    assert recorder.token_calls == 1

    clock.now += 2  # now within 60 s of expiry
    assert client.get_application_token() == "token-2"
    assert recorder.token_calls == 2


def test_token_type_must_be_bearer(make_client: MakeClient) -> None:
    client = make_client(
        lambda _: httpx.Response(
            200, json={"access_token": "x", "token_type": "mac", "expires_in": 3600}
        )
    )
    with pytest.raises(ApiError) as excinfo:
        client.get_application_token()
    assert excinfo.value.code == "SMARTCAR_ERROR"


def test_missing_credentials_is_not_configured(make_client: MakeClient, recorder: Recorder) -> None:
    client = make_client(recorder, client_secret="")
    with pytest.raises(ApiError) as excinfo:
        client.get_application_token()
    assert (excinfo.value.code, excinfo.value.status_code) == ("SMARTCAR_NOT_CONFIGURED", 503)
    assert recorder.requests == []


def test_rejected_token_is_refreshed_once(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/v3/vehicles/veh-1",
        httpx.Response(401, json={"errors": [{"code": "UNAUTHORIZED"}]}),
        httpx.Response(200, json=fixture_json("vehicle_bz.json")),
    )
    client = make_client(recorder)
    vehicle = client.get_vehicle("user-1", "veh-1")
    assert vehicle.attributes.model == "bZ XLE AWD"
    assert recorder.token_calls == 2
    auth_headers = [r.headers["Authorization"] for r in recorder.api_requests()]
    assert auth_headers == ["Bearer token-1", "Bearer token-2"]


def test_repeated_401_raises(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add(
        "GET", "/v3/vehicles/veh-1", httpx.Response(401, json={"errors": [{"code": "AUTH"}]})
    )
    client = make_client(recorder)
    with pytest.raises(ApiError) as excinfo:
        client.get_vehicle("user-1", "veh-1")
    assert excinfo.value.code == "SMARTCAR_ERROR"
    assert excinfo.value.details == {"upstream_status": 401, "upstream_code": "AUTH"}
    assert recorder.token_calls == 2


# ---- connect URL ---------------------------------------------------------------


def test_connect_url_parameters(make_client: MakeClient, recorder: Recorder) -> None:
    client = make_client(recorder, mode="simulated")
    url = client.build_connect_url(state="signed.state.jwt", external_id="profile-123")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == CONNECT_URL
    assert parse_qs(parts.query) == {
        "application_id": ["test-application-id"],
        "response_type": ["none"],
        "redirect_uri": ["http://localhost:8000/integrations/smartcar/callback"],
        "scope": ["read_battery read_charge read_odometer read_vehicle_info"],
        "state": ["signed.state.jwt"],
        "mode": ["simulated"],
        "external_id": ["profile-123"],
    }
    # Spaces must be %20-encoded, not "+".
    assert "scope=read_battery%20read_charge" in parts.query
    assert recorder.requests == []  # building the URL is offline


def test_connect_url_custom_scopes(make_client: MakeClient, recorder: Recorder) -> None:
    url = make_client(recorder).build_connect_url(
        state="s", external_id="p", scopes=["required:read_battery"]
    )
    assert parse_qs(urlsplit(url).query)["scope"] == ["required:read_battery"]


def test_connect_url_requires_application_id(make_client: MakeClient, recorder: Recorder) -> None:
    with pytest.raises(ApiError) as excinfo:
        make_client(recorder, application_id="").build_connect_url(state="s", external_id="p")
    assert excinfo.value.code == "SMARTCAR_NOT_CONFIGURED"


def test_connect_url_rejects_empty_scopes(make_client: MakeClient, recorder: Recorder) -> None:
    with pytest.raises(ValueError):
        make_client(recorder).build_connect_url(state="s", external_id="p", scopes=[])


# ---- connections and vehicles ---------------------------------------------------


def test_list_connections_parses_fixture(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add(
        "GET", "/v3/connections", httpx.Response(200, json=fixture_json("connections_list.json"))
    )
    connections = make_client(recorder).list_connections("redacted-id-1")

    assert [c.vehicle_id for c in connections] == ["redacted-id-3"]
    [request] = recorder.api_requests()
    assert request.headers["Authorization"] == "Bearer token-1"
    assert "sc-user-id" not in request.headers
    assert dict(request.url.params) == {
        "filter[userId]": "redacted-id-1",
        "page[number]": "1",
        "page[size]": "50",
    }


def _connection(n: int) -> dict:
    conn = json.loads(json.dumps(fixture_json("connections_list.json")["data"][0]))
    conn["id"] = f"conn-{n}"
    conn["relationships"]["vehicle"]["data"]["id"] = f"veh-{n}"
    return conn


def test_list_connections_follows_pages(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/v3/connections",
        httpx.Response(
            200, json={"data": [_connection(1), _connection(2)], "meta": {"totalCount": 3}}
        ),
        httpx.Response(200, json={"data": [_connection(3)], "meta": {"totalCount": 3}}),
    )
    connections = make_client(recorder).list_connections("user-1")
    assert [c.connection_id for c in connections] == ["conn-1", "conn-2", "conn-3"]
    pages = [r.url.params["page[number]"] for r in recorder.api_requests()]
    assert pages == ["1", "2"]


def test_list_connections_stops_on_empty_page(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/v3/connections",
        httpx.Response(200, json={"data": [_connection(1)], "meta": {"totalCount": 5}}),
        httpx.Response(200, json={"data": [], "meta": {"totalCount": 5}}),
    )
    assert len(make_client(recorder).list_connections("user-1")) == 1
    assert len(recorder.api_requests()) == 2


def test_get_signals_sends_user_header(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/v3/vehicles/redacted-id-3/signals",
        httpx.Response(200, json=fixture_json("signals_bz.json")),
    )
    response = make_client(recorder).get_signals("redacted-id-1", "redacted-id-3")
    assert len(response.data) == 27
    [request] = recorder.api_requests()
    assert request.headers["sc-user-id"] == "redacted-id-1"


def test_path_ids_are_escaped(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add(
        "GET", "/v3/vehicles/a/b", httpx.Response(200, json=fixture_json("vehicle_bz.json"))
    )
    make_client(recorder).get_vehicle("u", "a/b")
    assert recorder.api_requests()[0].url.raw_path == b"/v3/vehicles/a%2Fb"


def test_delete_connection(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add("DELETE", "/v3/connections/conn-1", httpx.Response(204))
    make_client(recorder).delete_connection("conn-1")
    [request] = recorder.api_requests()
    assert request.method == "DELETE"
    assert str(request.url) == f"{API_BASE}/connections/conn-1"


# ---- failures -------------------------------------------------------------------


def test_timeout_maps_to_504(make_client: MakeClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ApiError) as excinfo:
        make_client(handler).get_application_token()
    assert (excinfo.value.code, excinfo.value.status_code) == ("SMARTCAR_UNREACHABLE", 504)


def test_connection_error_maps_to_502(make_client: MakeClient, recorder: Recorder) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUTH_URL:
            return recorder(request)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiError) as excinfo:
        make_client(handler).list_connections("user-1")
    assert (excinfo.value.code, excinfo.value.status_code) == ("SMARTCAR_UNREACHABLE", 502)


def test_server_error_maps_to_502(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add("GET", "/v3/connections", httpx.Response(503, text="upstream down"))
    with pytest.raises(ApiError) as excinfo:
        make_client(recorder).list_connections("user-1")
    assert (excinfo.value.code, excinfo.value.status_code) == ("SMARTCAR_UNREACHABLE", 502)
    assert excinfo.value.details == {"upstream_status": 503}


def test_client_error_maps_to_smartcar_error(make_client: MakeClient, recorder: Recorder) -> None:
    recorder.add(
        "DELETE",
        "/v3/connections/conn-1",
        httpx.Response(404, json={"errors": [{"code": "RESOURCE_NOT_FOUND", "detail": "x"}]}),
    )
    with pytest.raises(ApiError) as excinfo:
        make_client(recorder).delete_connection("conn-1")
    assert (excinfo.value.code, excinfo.value.status_code) == ("SMARTCAR_ERROR", 502)
    assert excinfo.value.details == {"upstream_status": 404, "upstream_code": "RESOURCE_NOT_FOUND"}


def test_token_endpoint_error_maps_to_smartcar_error(make_client: MakeClient) -> None:
    client = make_client(lambda _: httpx.Response(401, json={"error": "invalid_client"}))
    with pytest.raises(ApiError) as excinfo:
        client.get_application_token()
    assert excinfo.value.details == {"upstream_status": 401, "upstream_code": "invalid_client"}


# ---- logging --------------------------------------------------------------------


def test_request_log_has_no_identifiers_or_tokens(
    make_client: MakeClient, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    recorder.add(
        "GET", "/v3/connections", httpx.Response(200, json=fixture_json("connections_list.json"))
    )
    recorder.add(
        "GET",
        "/v3/vehicles/redacted-id-3/signals",
        httpx.Response(200, json=fixture_json("signals_bz.json")),
    )
    client = make_client(recorder)
    with caplog.at_level(logging.DEBUG, logger="app.integrations.smartcar"):
        client.list_connections("redacted-id-1")
        client.get_signals("redacted-id-1", "redacted-id-3")

    request_logs = [r for r in caplog.records if r.getMessage() == "smartcar_request"]
    assert [(r.method, r.endpoint, r.status) for r in request_logs] == [  # type: ignore[attr-defined]
        ("POST", "oauth2/token", 200),
        ("GET", "connections", 200),
        ("GET", "vehicles/{id}/signals", 200),
    ]
    for record in caplog.records:
        text = json.dumps(record.__dict__, default=str)
        for secret in ("redacted-id-1", "redacted-id-3", "token-1", "test-client-secret"):
            assert secret not in text
