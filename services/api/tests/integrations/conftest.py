"""Helpers for Smartcar adapter tests. No network: every client uses MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.integrations.smartcar.client import SmartcarClient

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "smartcar"
TEST_MANAGEMENT_TOKEN = "chargewise-test-management-token"

API_BASE = "https://vehicle.test/v3"
AUTH_URL = "https://iam.test/oauth2/token"
CONNECT_URL = "https://connect.test/oauth/authorize"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> Any:
    return json.loads(fixture_bytes(name))


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def make_client(clock: FakeClock) -> Callable[..., SmartcarClient]:
    def _make(handler: Handler, **overrides: Any) -> SmartcarClient:
        kwargs: dict[str, Any] = {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "application_id": "test-application-id",
            "redirect_uri": "http://localhost:8000/integrations/smartcar/callback",
            "mode": "live",
            "api_base_url": API_BASE,
            "auth_url": AUTH_URL,
            "connect_url": CONNECT_URL,
            "timeout_seconds": 5.0,
            "transport": httpx.MockTransport(handler),
            "clock": clock,
        }
        kwargs.update(overrides)
        return SmartcarClient(**kwargs)

    return _make
