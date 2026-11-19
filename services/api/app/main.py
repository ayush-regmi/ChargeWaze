"""FastAPI application factory for Chargewise.

The HTTP surface lives under ``/api/v1``. See ``docs/api-contracts.md`` for
conventions. Modules under ``app/modules/*`` register their own routers via
``create_app``; integrations with third-party providers live under
``app/integrations/*`` and are always reached through a provider adapter.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.observability import init_sentry

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    settings = get_settings()
    init_sentry(settings)
    _quiet_http_client_logs()
    _redact_access_log_queries()

    app = FastAPI(
        title="Chargewise API",
        version="0.1.0",
        description=(
            "Chargewise backend. Authentication, vehicles, routing, trips, "
            "analytics, and ML inference. See docs/api-contracts.md for the "
            "public surface."
        ),
        docs_url="/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    # ---- Health ----
    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    # ---- Versioned routers ----
    v1 = APIRouter(prefix=API_PREFIX)
    _register_v1_routers(v1)
    app.include_router(v1)

    return app


# Every router the remaining phases plan to add is listed here up front
# (docs/plan.md §2.3), so feature branches built in parallel don't all edit this
# tuple. A module that doesn't exist yet is skipped.
V1_ROUTER_MODULES: tuple[str, ...] = (
    "app.modules.users.router",
    "app.modules.vehicles.router",
    "app.modules.vehicles.webhook_router",
    "app.modules.places.router",
    "app.modules.routing.router",
    "app.modules.stations.router",  # Phase 3 F5
    "app.modules.trips.router",  # Phase 4 F1
    "app.modules.trips.observed_router",  # Phase 5 F3
    "app.modules.analytics.router",  # Phase 5 F5
)


def _register_v1_routers(
    v1: APIRouter, module_paths: tuple[str, ...] = V1_ROUTER_MODULES
) -> None:
    """Attach module routers to the v1 prefix.

    Each module is imported lazily so a planned router that hasn't been built
    yet is skipped. Only a missing *router module* (or its package) is
    skipped: if a router exists but one of its own imports fails, the error
    is raised, so a broken module can't silently drop its endpoints.
    """

    import importlib

    for module_path in module_paths:
        try:
            mod = importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            if _is_planned_module_missing(module_path, exc.name):
                continue
            raise
        router = getattr(mod, "router", None)
        if router is not None:
            v1.include_router(router)


def _is_planned_module_missing(module_path: str, missing: str | None) -> bool:
    """True when ``missing`` is ``module_path`` itself or one of its parent packages."""

    if not missing:
        return False
    return module_path == missing or module_path.startswith(f"{missing}.")


def _quiet_http_client_logs() -> None:
    """Keep httpx/httpcore request logs (INFO/DEBUG) out of our logs.

    They print full request URLs, and Smartcar URLs embed vehicle and
    connection IDs (``/vehicles/{id}/signals``, ``/connections/{id}``), which
    must never be logged. Warnings and errors still come through.
    """

    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class _StripQueryString(logging.Filter):
    """Drop the query string from uvicorn access-log lines.

    Place search and station endpoints take search text and coordinates as
    query parameters, which must never be logged (phase3_plan.md D13). Uvicorn
    logs ``(client, method, path_with_query, http_version, status)``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def _redact_access_log_queries() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _StripQueryString) for f in access.filters):
        access.addFilter(_StripQueryString())


app = create_app()
