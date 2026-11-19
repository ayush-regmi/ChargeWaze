"""Sentry stays off without a DSN or in tests, and never sends user IDs or coordinates."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app.core.config import Settings
from app.core.observability import (
    FILTERED,
    init_sentry,
    scrub_event,
    scrub_string,
    sentry_enabled,
    start_sentry,
)

USER_ID = "3f2b9c1e-7a4d-4e8b-9c3a-2d1f0e6b5a49"
VEHICLE_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
LAT, LON = 37.774929, -122.419416
FAKE_DSN = "https://publickey@sentry.example.invalid/1"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"app_env": "production", "sentry_dsn_backend": FAKE_DSN}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


# ---- on/off ----


def test_disabled_without_dsn() -> None:
    settings = _settings(sentry_dsn_backend="")
    assert not sentry_enabled(settings)
    assert init_sentry(settings) is False


def test_disabled_in_test_env_even_with_dsn() -> None:
    assert not sentry_enabled(_settings(app_env="test"))


def test_disabled_under_pytest_even_with_dsn() -> None:
    # A developer's .env may carry a real DSN; running the suite must not send.
    assert init_sentry(_settings()) is False
    assert not sentry_sdk.get_client().is_active()


def test_default_settings_have_no_dsn() -> None:
    assert Settings(_env_file=None).sentry_dsn_backend == ""  # type: ignore[call-arg]


# ---- scrubber ----


def test_scrub_string_removes_uuids_and_coordinates() -> None:
    text = (
        f"user {USER_ID} vehicle {VEHICLE_ID} at ({LAT}, {LON}) "
        f"GET /stations?lat={LAT}&lon={LON}&radius=5 "
        f"Point(lat={LAT}, lon={LON}) {{'origin_lat': {LAT}, 'destLng': {LON}}}"
    )
    out = scrub_string(text)
    assert USER_ID not in out
    assert VEHICLE_ID not in out
    assert str(LAT) not in out
    assert str(LON) not in out
    assert "radius=5" in out


def test_scrub_event_removes_personal_data() -> None:
    event: dict[str, Any] = {
        "message": f"plan failed for {USER_ID}",
        "user": {"id": USER_ID, "email": "someone@example.com", "ip_address": "1.2.3.4"},
        "request": {
            "url": f"https://api.example.com/api/v1/stations?lat={LAT}&lon={LON}",
            "query_string": f"lat={LAT}&lon={LON}",
            "data": {"origin": {"lat": LAT, "lon": LON}},
            "cookies": {"sb-access-token": "secret"},
            "headers": {"Authorization": "Bearer abc.def.ghi", "User-Agent": "pytest"},
        },
        "tags": {"user_id": USER_ID, "module": "routing"},
        "extra": {
            "vehicle_id": VEHICLE_ID,
            "origin_lat": LAT,
            "dest_lon": LON,
            "coordinates": [[LON, LAT]],
            "polyline": "_p~iF~ps|U_ulLnnqC",
            "stop_count": 3,
            "waypoints": [(LAT, LON), [str(LAT), str(LON)]],
        },
        "breadcrumbs": {
            "values": [
                {"message": f"vehicle at {LAT},{LON}", "data": {"latitude": LAT}},
                {
                    "category": "httplib",
                    "data": {"url": "https://api.mapbox.com/search?q=1+Main+St&access_token=pk.x"},
                },
            ]
        },
    }

    out = scrub_event(event)
    dumped = json.dumps(out)

    for secret in (
        USER_ID,
        VEHICLE_ID,
        str(LAT),
        str(LON),
        "someone@example.com",
        "1.2.3.4",
        "abc.def.ghi",
        "_p~iF~ps|U_ulLnnqC",
        "sb-access-token",
        "Main+St",
        "pk.x",
    ):
        assert secret not in dumped, secret
    assert "user" not in out
    assert "query_string" not in out["request"]
    assert "data" not in out["request"]
    assert "cookies" not in out["request"]
    assert out["request"]["url"] == "https://api.example.com/api/v1/stations"
    assert out["request"]["headers"]["Authorization"] == FILTERED
    assert out["tags"] == {"user_id": FILTERED, "module": "routing"}
    # Non-sensitive values are kept so events stay useful.
    assert out["extra"]["stop_count"] == 3
    assert out["request"]["headers"]["User-Agent"] == "pytest"
    # The input is not mutated.
    assert event["user"]["id"] == USER_ID


# ---- end to end through the SDK ----


class _CaptureTransport(Transport):
    """Keeps envelopes in memory instead of sending them."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    def payloads(self) -> str:
        parts = []
        for envelope in self.envelopes:
            for item in envelope.items:
                parts.append(item.payload.get_bytes().decode("utf-8"))
        return "\n".join(parts)


@pytest.fixture
def captured() -> Iterator[_CaptureTransport]:
    transport = _CaptureTransport()
    start_sentry(dsn=FAKE_DSN, environment="production", transport=transport)
    try:
        yield transport
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init()  # back to a disabled client


def test_unhandled_route_error_is_scrubbed_before_sending(captured: _CaptureTransport) -> None:
    app = FastAPI()

    @app.get("/boom/{vehicle_id}")
    def boom(vehicle_id: str, lat: float, lon: float) -> None:
        user_id = USER_ID
        point = (lat, lon)
        logging.getLogger("chargewise.test").info("planning for %s at %s", user_id, point)
        raise RuntimeError(f"no route for {user_id} near {point}")

    sentry_sdk.set_user({"id": USER_ID})
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/boom/{VEHICLE_ID}?lat={LAT}&lon={LON}")
    assert response.status_code == 500
    sentry_sdk.flush()

    sent = captured.payloads()
    assert "RuntimeError" in sent, "the event should still be reported"
    for secret in (USER_ID, VEHICLE_ID, str(LAT), str(LON)):
        assert secret not in sent, sent[max(0, sent.find(secret) - 300) : sent.find(secret) + 60]
