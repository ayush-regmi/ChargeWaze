"""Request metrics, JSON logs and ``GET /metrics`` (P6-F4, architecture.md §9).

Three pieces, wired by :func:`install_monitoring`:

- **JSON logs.** The ``app.*`` loggers write one JSON object per line to
  stdout (Render keeps stdout as the service log). Extra fields go through the
  Sentry scrubber, so user/vehicle IDs, UUIDs and coordinates never reach a log.
- **Request metrics.** An ASGI middleware times every request and records it
  under its route *template* (``/api/v1/trips/{trip_id}``), never the raw path
  or query string. Each API request also gets one ``http_request`` log line.
  ``POST /routes/plan`` slower than ``MONITORING_PLANNER_SLOW_MS`` logs a
  ``planner_slow`` warning.
- **``GET /metrics``.** A JSON snapshot of those counters: per-route counts,
  status classes and latency percentiles, the planner's latency and the
  Smartcar webhook success rate. Hidden (404) unless
  ``MONITORING_METRICS_TOKEN`` is set, then it needs that bearer token.

Counters live in process memory and start at zero on every restart. On
Render's free plan the service sleeps after 15 idle minutes, so a snapshot
only covers the time since the last cold start.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.observability import scrub_event, scrub_string

logger = logging.getLogger("app.monitoring")

PLAN_ROUTE = "POST /api/v1/routes/plan"
WEBHOOK_ROUTE = "POST /api/v1/webhooks/smartcar"
# Requests that matched no route are grouped here, so unknown paths (which can
# carry IDs) never become metric keys or log values.
UNMATCHED_ROUTE = "unmatched"
# Not logged per request: health probes (Render, smoke workflow) and scrapes.
_UNLOGGED_PATHS = frozenset({"/health", "/metrics"})
# Percentiles are computed over each route's most recent requests.
LATENCY_WINDOW = 500


# ---------------------------------------------------------------- metrics


@dataclass
class _RouteStats:
    count: int = 0
    status: dict[str, int] = field(default_factory=dict)
    max_ms: float = 0.0
    recent_ms: deque[float] = field(default_factory=lambda: deque(maxlen=LATENCY_WINDOW))


def _percentile(sorted_values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; ``None`` for no data."""

    if not sorted_values:
        return None
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _latency(stats: _RouteStats | None) -> dict[str, Any]:
    values = sorted(stats.recent_ms) if stats else []
    p50 = _percentile(values, 0.50)
    p95 = _percentile(values, 0.95)
    return {
        "count": stats.count if stats else 0,
        "p50_ms": None if p50 is None else round(p50, 1),
        "p95_ms": None if p95 is None else round(p95, 1),
        "max_ms": round(stats.max_ms, 1) if stats else None,
    }


class RequestMetrics:
    """Thread-safe in-memory request counters, keyed by ``"METHOD /route/template"``."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._routes: dict[str, _RouteStats] = {}
        self._started_at = self._clock()

    def record(self, key: str, status_code: int, duration_ms: float) -> None:
        status_class = f"{status_code // 100}xx"
        with self._lock:
            stats = self._routes.setdefault(key, _RouteStats())
            stats.count += 1
            stats.status[status_class] = stats.status.get(status_class, 0) + 1
            # Webhook outcomes need the exact code (401 is excluded from the rate).
            if key == WEBHOOK_ROUTE:
                code = str(status_code)
                stats.status[code] = stats.status.get(code, 0) + 1
            stats.max_ms = max(stats.max_ms, duration_ms)
            stats.recent_ms.append(duration_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            routes = {
                key: {**_latency(stats), "status": dict(sorted(stats.status.items()))}
                for key, stats in sorted(self._routes.items())
            }
            planner = _latency(self._routes.get(PLAN_ROUTE))
            webhooks = self._webhook_summary(self._routes.get(WEBHOOK_ROUTE))
        return {
            "started_at": self._started_at.isoformat().replace("+00:00", "Z"),
            "uptime_seconds": round((now - self._started_at).total_seconds(), 1),
            "latency_window": LATENCY_WINDOW,
            "planner": planner,
            "webhooks": webhooks,
            "routes": routes,
        }

    @staticmethod
    def _webhook_summary(stats: _RouteStats | None) -> dict[str, Any]:
        """Share of Smartcar deliveries answered 2xx.

        ``401`` (bad or missing signature) is left out: those requests are not
        Smartcar deliveries (probes, forgeries, the smoke workflow). ``400``
        (signed but malformed) and ``5xx`` (ingest failed; Smartcar retries)
        count as failures.
        """

        if stats is None:
            return {"deliveries": 0, "succeeded": 0, "failed": 0, "success_rate": None}
        rejected = stats.status.get("401", 0)
        deliveries = stats.count - rejected
        succeeded = stats.status.get("2xx", 0)
        return {
            "deliveries": deliveries,
            "succeeded": succeeded,
            "failed": deliveries - succeeded,
            "signature_rejected": rejected,
            "success_rate": round(succeeded / deliveries, 4) if deliveries else None,
        }


def _route_template(scope: Scope) -> str | None:
    """The matched route's full path template, or ``None`` when nothing matched.

    FastAPI (0.16x) keeps included routers nested, so ``scope["route"].path``
    lacks the ``/api/v1`` prefix; the full template is on the effective route
    context. ``test_requests_are_keyed_by_route_template`` pins this down.
    """

    fastapi_scope = scope.get("fastapi")
    context = (
        fastapi_scope.get("effective_route_context") if isinstance(fastapi_scope, dict) else None
    )
    template = getattr(context, "path", None)
    if isinstance(template, str):
        return template
    template = getattr(scope.get("route"), "path", None)
    return template if isinstance(template, str) else None


def _route_key(scope: Scope) -> str:
    template = _route_template(scope)
    if template is None:
        return UNMATCHED_ROUTE
    return f"{scope.get('method', 'GET')} {template}"


class RequestMetricsMiddleware:
    """Time each HTTP request, record it, and log one ``http_request`` line.

    The time stops when the last response byte is sent, so background tasks
    that run after the response (webhook ingest hooks) aren't counted.
    """

    def __init__(self, app: ASGIApp, *, metrics: RequestMetrics, planner_slow_ms: float) -> None:
        self.app = app
        self.metrics = metrics
        self.planner_slow_ms = planner_slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500
        finished_at: float | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, finished_at
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            elif message["type"] == "http.response.body" and not message.get("more_body"):
                finished_at = time.perf_counter()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            end = finished_at if finished_at is not None else time.perf_counter()
            self._observe(scope, status_code, (end - start) * 1000)

    def _observe(self, scope: Scope, status_code: int, duration_ms: float) -> None:
        key = _route_key(scope)
        self.metrics.record(key, status_code, duration_ms)
        if scope.get("path") in _UNLOGGED_PATHS:
            return
        fields = {
            "method": scope.get("method"),
            "route": key.partition(" ")[2] or key,
            "status": status_code,
            "duration_ms": round(duration_ms, 1),
        }
        logger.info("http_request", extra=fields)
        if key == PLAN_ROUTE and duration_ms > self.planner_slow_ms:
            logger.warning(
                "planner_slow",
                extra={
                    "status": status_code,
                    "duration_ms": round(duration_ms, 1),
                    "threshold_ms": self.planner_slow_ms,
                },
            )


# ---------------------------------------------------------------- JSON logs

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}
# Extra fields logged as-is. Smartcar event IDs are delivery IDs (no user or
# vehicle identity) and are what you'd search for in Smartcar's dashboard.
_PASSTHROUGH_FIELDS = frozenset({"event_id"})


class JsonFormatter(logging.Formatter):
    """One JSON object per record, scrubbed like a Sentry event.

    Tracebacks are not written (frames and exception messages can echo values);
    only the exception type is. Sentry holds the full error when it is on.
    """

    def format(self, record: logging.LogRecord) -> str:
        extras = {
            key: value
            for key, value in vars(record).items()
            if key not in _RECORD_ATTRS and not key.startswith("_")
        }
        passthrough = {k: extras.pop(k) for k in _PASSTHROUGH_FIELDS & extras.keys()}
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": scrub_string(record.getMessage()),
        }
        # ``scrub_event`` drops ``user``/``request`` keys and filters IDs and
        # coordinates by key name and in free text.
        payload.update(scrub_event(extras))
        payload.update({k: v for k, v in passthrough.items() if isinstance(v, str | None)})
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


class _JsonStdoutHandler(logging.StreamHandler):
    """Marker type so :func:`configure_logging` stays idempotent."""


def logging_enabled(settings: Settings) -> bool:
    """JSON logs go to stdout outside tests (pytest's ``caplog`` still works)."""

    return not settings.is_test and "pytest" not in sys.modules


def configure_logging(settings: Settings, *, force: bool = False) -> bool:
    """Send ``app.*`` INFO+ logs to stdout as JSON. Returns whether a handler is installed."""

    if not (force or logging_enabled(settings)):
        return False
    app_logger = logging.getLogger("app")
    if not any(isinstance(h, _JsonStdoutHandler) for h in app_logger.handlers):
        handler = _JsonStdoutHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    # Root has no handler under uvicorn; don't print app records twice if it ever does.
    app_logger.propagate = False
    return True


# ---------------------------------------------------------------- /metrics


def _metrics_not_found() -> ApiError:
    return ApiError("NOT_FOUND", "Not Found", status_code=404)


def metrics_endpoint(request: Request, settings: Settings = Depends(get_settings)) -> JSONResponse:
    token = settings.monitoring_metrics_token
    if not token:
        raise _metrics_not_found()
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        presented.strip().encode(), token.encode()
    ):
        raise ApiError("UNAUTHENTICATED", "A valid metrics token is required.", status_code=401)
    metrics: RequestMetrics = request.app.state.request_metrics
    return JSONResponse(metrics.snapshot(), headers={"Cache-Control": "no-store"})


def install_monitoring(app: FastAPI, settings: Settings) -> RequestMetrics:
    """Wire JSON logs, the metrics middleware and ``GET /metrics`` into ``app``."""

    configure_logging(settings)
    metrics = RequestMetrics()
    app.state.request_metrics = metrics
    app.add_middleware(
        RequestMetricsMiddleware,
        metrics=metrics,
        planner_slow_ms=settings.monitoring_planner_slow_ms,
    )
    app.add_api_route("/metrics", metrics_endpoint, methods=["GET"], include_in_schema=False)
    return metrics


__all__ = [
    "PLAN_ROUTE",
    "UNMATCHED_ROUTE",
    "WEBHOOK_ROUTE",
    "JsonFormatter",
    "RequestMetrics",
    "RequestMetricsMiddleware",
    "configure_logging",
    "install_monitoring",
]
