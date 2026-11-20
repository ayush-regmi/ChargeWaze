"""Per-user rate limits on ``/routes/plan`` and ``/places/suggest`` (P6-F4).

``app/modules/auth/rate_limit.py``; contract: api-contracts §1.1 ``RATE_LIMITED``,
§3.7 and §3.13.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.integrations.mapbox.client import MapboxClient, get_mapbox_client
from app.main import create_app
from app.modules.auth.rate_limit import Limit, TokenBucketLimiter, limit_for
from tests.conftest import _KeyMaterial, _StubVerifier, make_token

SUGGEST = "/api/v1/places/suggest"
PLAN = "/api/v1/routes/plan"
SUGGEST_PARAMS = {"q": "Toronto", "session_token": "6f1c2b9e-1d1e-4a3b-9b1e-0c6a1f2d3e4f"}
OTHER_USER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, app_env="test", **overrides)  # type: ignore[call-arg]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---- TokenBucketLimiter ---------------------------------------------------------


def test_bucket_allows_burst_then_waits_for_refill() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(clock=clock)
    limit = Limit(capacity=3, refill_per_second=0.5)  # one token every 2 s
    key = ("plan", "u1")

    assert [limiter.acquire(key, limit) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.acquire(key, limit) == pytest.approx(2.0)

    clock.now += 1.0  # half a token
    assert limiter.acquire(key, limit) == pytest.approx(1.0)
    clock.now += 1.0
    assert limiter.acquire(key, limit) == 0.0
    assert limiter.acquire(key, limit) == pytest.approx(2.0)


def test_bucket_refills_to_capacity_only() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(clock=clock)
    limit = Limit(capacity=2, refill_per_second=1.0)
    key = ("suggest", "u1")
    limiter.acquire(key, limit)
    clock.now += 3600
    assert [limiter.acquire(key, limit) for _ in range(2)] == [0.0, 0.0]
    assert limiter.acquire(key, limit) > 0


def test_buckets_are_separate_per_user_and_endpoint() -> None:
    limiter = TokenBucketLimiter(clock=FakeClock())
    limit = Limit(capacity=1, refill_per_second=0.001)
    assert limiter.acquire(("plan", "u1"), limit) == 0.0
    assert limiter.acquire(("plan", "u1"), limit) > 0
    assert limiter.acquire(("plan", "u2"), limit) == 0.0
    assert limiter.acquire(("suggest", "u1"), limit) == 0.0


def test_limiter_drops_least_recently_used_buckets() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(clock=clock, max_keys=10)
    limit = Limit(capacity=1, refill_per_second=0.001)
    for i in range(11):
        clock.now += 1
        limiter.acquire(("plan", f"u{i}"), limit)
    assert len(limiter) == 9
    # The oldest user was dropped and starts with a full bucket again.
    assert limiter.acquire(("plan", "u0"), limit) == 0.0
    assert limiter.acquire(("plan", "u10"), limit) > 0


def test_limits_come_from_settings() -> None:
    settings = _settings(
        rate_limit_plan_burst=4,
        rate_limit_plan_per_hour=36,
        rate_limit_suggest_burst=7,
        rate_limit_suggest_per_minute=30,
    )
    assert limit_for("plan", settings) == Limit(capacity=4, refill_per_second=0.01)
    assert limit_for("suggest", settings) == Limit(capacity=7, refill_per_second=0.5)


# ---- /places/suggest ------------------------------------------------------------


@pytest.fixture()
def settings() -> Settings:
    return _settings(rate_limit_suggest_burst=2, rate_limit_suggest_per_minute=1)


@pytest.fixture()
def places_app(key_material: _KeyMaterial, settings: Settings) -> Iterator[FastAPI]:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"suggestions": []})

    client = MapboxClient(
        access_token="sk.test",
        api_base_url="https://mapbox.test",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(respond),
    )
    app = create_app()
    app.state.jwt_verifier = _StubVerifier(key_material)
    app.dependency_overrides[get_mapbox_client] = lambda: client
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        client.close()


def _auth(key_material: _KeyMaterial, sub: uuid.UUID | None = None) -> dict[str, str]:
    token = make_token(key_material, sub=sub) if sub else make_token(key_material)
    return {"Authorization": f"Bearer {token}"}


def test_suggest_returns_429_after_burst(places_app: FastAPI, key_material: _KeyMaterial) -> None:
    client = TestClient(places_app)
    headers = _auth(key_material)
    for _ in range(2):
        assert client.get(SUGGEST, params=SUGGEST_PARAMS, headers=headers).status_code == 200

    response = client.get(SUGGEST, params=SUGGEST_PARAMS, headers=headers)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json() == {
        "error": {
            "code": "RATE_LIMITED",
            "message": "Too many place searches. Try again shortly.",
            "details": {"limit": "suggest", "retry_after_seconds": 60},
        }
    }

    # Another user has their own bucket.
    other = _auth(key_material, OTHER_USER)
    assert client.get(SUGGEST, params=SUGGEST_PARAMS, headers=other).status_code == 200


def test_unauthenticated_requests_do_not_spend_tokens(
    places_app: FastAPI, key_material: _KeyMaterial
) -> None:
    client = TestClient(places_app)
    for _ in range(5):
        assert client.get(SUGGEST, params=SUGGEST_PARAMS).status_code == 401
    headers = _auth(key_material)
    assert client.get(SUGGEST, params=SUGGEST_PARAMS, headers=headers).status_code == 200


def test_rate_limit_can_be_switched_off(
    places_app: FastAPI, key_material: _KeyMaterial, settings: Settings
) -> None:
    places_app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"rate_limit_enabled": False}
    )
    client = TestClient(places_app)
    headers = _auth(key_material)
    for _ in range(5):
        assert client.get(SUGGEST, params=SUGGEST_PARAMS, headers=headers).status_code == 200


def test_each_app_has_its_own_limiter(key_material: _KeyMaterial, settings: Settings) -> None:
    # Guards the test suite: a process-wide limiter would leak spent tokens
    # between tests that share TEST_USER_ID.
    a, b = create_app(), create_app()
    for app in (a, b):
        app.state.jwt_verifier = _StubVerifier(key_material)
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_mapbox_client] = lambda: MapboxClient(
            access_token="sk.test",
            api_base_url="https://mapbox.test",
            timeout_seconds=5.0,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"suggestions": []})),
        )
    headers = _auth(key_material)
    for _ in range(2):
        TestClient(a).get(SUGGEST, params=SUGGEST_PARAMS, headers=headers)
    assert TestClient(a).get(SUGGEST, params=SUGGEST_PARAMS, headers=headers).status_code == 429
    assert TestClient(b).get(SUGGEST, params=SUGGEST_PARAMS, headers=headers).status_code == 200


# ---- /routes/plan ---------------------------------------------------------------


def test_plan_is_rate_limited(app_with_db: FastAPI, key_material: _KeyMaterial) -> None:
    app_with_db.dependency_overrides[get_settings] = lambda: _settings(
        rate_limit_plan_burst=2, rate_limit_plan_per_hour=60
    )
    client = TestClient(app_with_db)
    headers = _auth(key_material)
    # Every authenticated request spends a token, even one that fails validation.
    for _ in range(2):
        assert client.post(PLAN, json={}, headers=headers).status_code == 422

    response = client.post(PLAN, json={}, headers=headers)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    body = response.json()["error"]
    assert body["code"] == "RATE_LIMITED"
    assert body["details"] == {"limit": "plan", "retry_after_seconds": 60}
