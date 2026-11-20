"""Request metrics, JSON logs and ``GET /metrics`` (P6-F4, ``app/core/monitoring.py``)."""

from __future__ import annotations

import io
import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.monitoring import (
    PLAN_ROUTE,
    UNMATCHED_ROUTE,
    WEBHOOK_ROUTE,
    JsonFormatter,
    RequestMetrics,
    RequestMetricsMiddleware,
    configure_logging,
)
from app.integrations.smartcar.signatures import compute_signature
from app.main import create_app

METRICS_TOKEN = "metrics-test-token"
WEBHOOK = "/api/v1/webhooks/smartcar"
MANAGEMENT_TOKEN = "test-management-token"
SMARTCAR_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "smartcar"


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, app_env="test", **overrides)  # type: ignore[call-arg]


@pytest.fixture()
def app() -> Iterator[FastAPI]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: _settings(
        monitoring_metrics_token=METRICS_TOKEN
    )
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


def _snapshot(client: TestClient) -> dict[str, Any]:
    response = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ---- RequestMetrics -----------------------------------------------------------


def test_snapshot_counts_status_classes_and_percentiles() -> None:
    metrics = RequestMetrics()
    for ms in range(1, 101):  # 1..100 ms
        metrics.record(PLAN_ROUTE, 200, float(ms))
    metrics.record(PLAN_ROUTE, 502, 250.0)

    snap = metrics.snapshot()
    plan = snap["routes"][PLAN_ROUTE]
    assert plan["count"] == 101
    assert plan["status"] == {"2xx": 100, "5xx": 1}
    assert snap["planner"]["count"] == 101
    assert snap["planner"]["p50_ms"] == 51.0
    assert snap["planner"]["p95_ms"] == 96.0
    assert snap["planner"]["max_ms"] == 250.0


def test_empty_snapshot_has_no_percentiles_or_rate() -> None:
    snap = RequestMetrics().snapshot()
    assert snap["planner"] == {"count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    assert snap["webhooks"]["success_rate"] is None
    assert snap["routes"] == {}


def test_latency_window_keeps_recent_requests_only() -> None:
    metrics = RequestMetrics()
    for _ in range(600):
        metrics.record(PLAN_ROUTE, 200, 10_000.0)
    for _ in range(500):
        metrics.record(PLAN_ROUTE, 200, 10.0)
    planner = metrics.snapshot()["planner"]
    assert planner["count"] == 1100
    assert planner["p95_ms"] == 10.0
    assert planner["max_ms"] == 10_000.0  # max is since start, not windowed


def test_webhook_success_rate_ignores_signature_rejections() -> None:
    metrics = RequestMetrics()
    for _ in range(8):
        metrics.record(WEBHOOK_ROUTE, 200, 5.0)
    metrics.record(WEBHOOK_ROUTE, 400, 5.0)
    metrics.record(WEBHOOK_ROUTE, 500, 5.0)
    for _ in range(5):
        metrics.record(WEBHOOK_ROUTE, 401, 1.0)

    webhooks = metrics.snapshot()["webhooks"]
    assert webhooks == {
        "deliveries": 10,
        "succeeded": 8,
        "failed": 2,
        "signature_rejected": 5,
        "success_rate": 0.8,
    }


def test_uptime_uses_clock() -> None:
    start = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    times = iter([start, start + timedelta(seconds=90)])
    snap = RequestMetrics(clock=lambda: next(times)).snapshot()
    assert snap["started_at"] == "2026-11-19T12:00:00Z"
    assert snap["uptime_seconds"] == 90.0


# ---- middleware on the real app --------------------------------------------------


def test_requests_are_keyed_by_route_template(app: FastAPI) -> None:
    client = TestClient(app)
    trip_id = uuid.uuid4()
    assert client.get(f"/api/v1/trips/{trip_id}").status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get(f"/nope/{trip_id}").status_code == 404

    routes = _snapshot(client)["routes"]
    assert routes["GET /api/v1/trips/{trip_id}"]["status"] == {"4xx": 1}
    assert routes["GET /health"]["count"] == 1
    assert routes[UNMATCHED_ROUTE]["status"] == {"4xx": 1}
    assert str(trip_id) not in json.dumps(routes)


def test_request_log_line_has_template_and_no_query(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="app.monitoring"):
        client.get(
            "/api/v1/places/suggest",
            params={"q": "Toronto", "session_token": str(uuid.uuid4()), "lat": 43.6, "lon": -79.4},
        )
        client.get("/health")

    lines = [r for r in caplog.records if r.getMessage() == "http_request"]
    assert len(lines) == 1  # /health isn't logged
    [line] = lines
    assert line.__dict__["route"] == "/api/v1/places/suggest"
    assert line.__dict__["method"] == "GET"
    assert line.__dict__["status"] == 401
    assert isinstance(line.__dict__["duration_ms"], float)
    rendered = JsonFormatter().format(line)
    assert "Toronto" not in rendered and "43.6" not in rendered and "?" not in rendered


def test_unhandled_exception_counts_as_500() -> None:
    metrics = RequestMetrics()
    inner = FastAPI()

    @inner.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    inner.add_middleware(RequestMetricsMiddleware, metrics=metrics, planner_slow_ms=1e9)
    response = TestClient(inner, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert metrics.snapshot()["routes"]["GET /boom"]["status"] == {"5xx": 1}


def test_background_tasks_are_not_timed() -> None:
    metrics = RequestMetrics()
    inner = FastAPI()

    @inner.get("/fast")
    def fast(tasks: BackgroundTasks) -> dict[str, bool]:
        tasks.add_task(time.sleep, 0.3)
        return {"ok": True}

    inner.add_middleware(RequestMetricsMiddleware, metrics=metrics, planner_slow_ms=1e9)
    TestClient(inner).get("/fast")
    assert metrics.snapshot()["routes"]["GET /fast"]["max_ms"] < 250


def test_slow_plan_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    metrics = RequestMetrics()
    inner = FastAPI()

    @inner.post("/api/v1/routes/plan")
    def plan() -> dict[str, bool]:
        time.sleep(0.02)
        return {"ok": True}

    inner.add_middleware(RequestMetricsMiddleware, metrics=metrics, planner_slow_ms=5.0)
    with caplog.at_level(logging.INFO, logger="app.monitoring"):
        TestClient(inner).post("/api/v1/routes/plan")
    [slow] = [r for r in caplog.records if r.getMessage() == "planner_slow"]
    assert slow.levelno == logging.WARNING
    assert slow.__dict__["threshold_ms"] == 5.0
    assert slow.__dict__["duration_ms"] >= 5.0
    assert metrics.snapshot()["planner"]["count"] == 1


def test_webhook_outcomes_feed_success_rate(app_with_db: FastAPI) -> None:
    app_with_db.dependency_overrides[get_settings] = lambda: _settings(
        monitoring_metrics_token=METRICS_TOKEN, smartcar_management_token=MANAGEMENT_TOKEN
    )
    client = TestClient(app_with_db)

    verify = (SMARTCAR_FIXTURES / "webhook_verify_challenge.json").read_bytes()
    malformed = b"not json"
    for body, signed in ((verify, True), (malformed, True), (verify, False)):
        headers = {"Content-Type": "application/json"}
        if signed:
            headers["SC-Signature"] = compute_signature(body, MANAGEMENT_TOKEN)
        client.post(WEBHOOK, content=body, headers=headers)

    snap = _snapshot(client)
    assert snap["routes"][WEBHOOK_ROUTE]["status"] == {
        "200": 1,
        "2xx": 1,
        "400": 1,
        "401": 1,
        "4xx": 2,
    }
    assert snap["webhooks"]["deliveries"] == 2
    assert snap["webhooks"]["succeeded"] == 1
    assert snap["webhooks"]["signature_rejected"] == 1
    assert snap["webhooks"]["success_rate"] == 0.5


# ---- GET /metrics ---------------------------------------------------------------


def test_metrics_hidden_without_token() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: _settings()
    response = TestClient(app).get("/metrics", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {METRICS_TOKEN}"}],
)
def test_metrics_requires_token(app: FastAPI, headers: dict[str, str]) -> None:
    response = TestClient(app).get("/metrics", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_metrics_snapshot_shape(app: FastAPI) -> None:
    client = TestClient(app)
    response = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {
        "started_at",
        "uptime_seconds",
        "latency_window",
        "planner",
        "webhooks",
        "routes",
    }


def test_metrics_not_in_openapi(app: FastAPI) -> None:
    paths = TestClient(app).get("/api/v1/openapi.json").json()["paths"]
    assert "/metrics" not in paths


# ---- JSON logs ------------------------------------------------------------------


def _record(msg: str, **extra: Any) -> logging.LogRecord:
    record = logging.makeLogRecord({"name": "app.test", "levelname": "INFO", "msg": msg})
    record.__dict__.update(extra)
    return record


def test_json_formatter_scrubs_ids_and_coordinates() -> None:
    user_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    record = _record(
        f"planned for {user_id} at 39.4143,-77.4105",
        user_id=user_id,
        vehicle_id="veh-1",
        lat=39.41,
        origin_lon=-77.41,
        reason=f"vehicle {user_id} unknown",
        event_id=event_id,
        status=200,
    )
    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["ts"].endswith("Z")
    assert payload["status"] == 200
    assert payload["event_id"] == event_id  # Smartcar delivery IDs stay searchable
    assert payload["user_id"] == "[Filtered]"
    assert payload["vehicle_id"] == "[Filtered]"
    assert payload["lat"] == "[Filtered]"
    assert payload["origin_lon"] == "[Filtered]"
    assert user_id not in payload["event"] and "39.4143" not in payload["event"]
    assert user_id not in payload["reason"]


def test_json_formatter_writes_exception_type_only() -> None:
    try:
        raise ValueError("secret value 39.4143,-77.4105")
    except ValueError:
        record = _record("failed", exc_info=sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert payload["exc_type"] == "ValueError"
    assert "secret value" not in json.dumps(payload)


def test_configure_logging_is_off_in_tests() -> None:
    assert configure_logging(_settings()) is False


def test_configure_logging_writes_json_once(monkeypatch: pytest.MonkeyPatch) -> None:
    app_logger = logging.getLogger("app")
    saved = (list(app_logger.handlers), app_logger.level, app_logger.propagate)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    try:
        assert configure_logging(_settings(), force=True)
        assert configure_logging(_settings(), force=True)  # idempotent
        logging.getLogger("app.monitoring").info("hello", extra={"status": 204})
        lines = stream.getvalue().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["status"] == 204
    finally:
        app_logger.handlers[:] = saved[0]
        app_logger.setLevel(saved[1])
        app_logger.propagate = saved[2]
