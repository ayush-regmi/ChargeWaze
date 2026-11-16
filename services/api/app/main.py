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

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    settings = get_settings()
    _quiet_http_client_logs()

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


def _register_v1_routers(v1: APIRouter) -> None:
    """Attach module routers to the v1 prefix.

    Each module is imported lazily so that scaffolding slices do not need every
    downstream module (users, vehicles, routing, ...) to exist yet. Once every
    expected module is wired up, this can become a plain list of imports.
    """

    import importlib

    for module_path in (
        "app.modules.users.router",
        "app.modules.vehicles.router",
        "app.modules.vehicles.webhook_router",
    ):
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        router = getattr(mod, "router", None)
        if router is not None:
            v1.include_router(router)


def _quiet_http_client_logs() -> None:
    """Keep httpx/httpcore request logs (INFO/DEBUG) out of our logs.

    They print full request URLs, and Smartcar URLs embed vehicle and
    connection IDs (``/vehicles/{id}/signals``, ``/connections/{id}``), which
    must never be logged. Warnings and errors still come through.
    """

    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


app = create_app()
