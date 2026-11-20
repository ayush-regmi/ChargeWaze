"""Public demo vs private live mode (P6-F2, architecture.md §7.2).

``get_current_user`` calls :func:`enforce_access` after it has verified the
token, so every user route is gated without router changes. Webhooks and
``/health`` don't authenticate a user and aren't gated.

**Live mode** (``APP_PUBLIC_DEMO_MODE=false``): only the Supabase user IDs in
``LIVE_ALLOWED_USER_IDS`` get in. An empty list means no restriction in
development and test, and nobody under ``APP_ENV=production`` (fails closed).

**Demo mode** (``APP_PUBLIC_DEMO_MODE=true``): the deployment serves one
shared, seeded demo account (``scripts/seed_demo.py``), so it is read-only
except for planning. Demo data and real data never share a database: if any
real (``is_simulated = false``) vehicle exists, every request is refused
before a row is read. Telemetry, planned trips and observed legs all hang off
a vehicle (``ON DELETE CASCADE``), so a database with no real vehicle has no
real user data. Settings also refuse to load Smartcar secrets in demo mode,
so no real vehicle can be connected or report telemetry.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError

logger = logging.getLogger(__name__)

# The only writes a demo visitor may make: planning reads providers and stores
# an unsaved plan for the demo account; nothing a later visitor depends on.
DEMO_WRITE_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/routes/plan"),
        ("POST", "/api/v1/routes/directions"),
        ("POST", "/api/v1/stations/along-route"),
    }
)
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Reads another module's table on purpose: this is the one cross-cutting guard
# that must run before any module's query (docs/plan.md §4.4 P6-F2).
_REAL_VEHICLE_EXISTS = text("SELECT EXISTS (SELECT 1 FROM vehicles WHERE NOT is_simulated)")


def enforce_access(
    request: Request,
    *,
    user_id: uuid.UUID,
    settings: Settings,
    db: Session,
) -> None:
    """Raise unless ``user_id`` may make this request in this deployment's mode."""

    if settings.app_public_demo_mode:
        _enforce_demo(request, db)
    else:
        _enforce_live(user_id, settings)


def _enforce_live(user_id: uuid.UUID, settings: Settings) -> None:
    allowlist = settings.live_allowlist
    if not allowlist and settings.app_env != "production":
        return
    if user_id in allowlist:
        return
    raise ApiError(
        "FORBIDDEN",
        "This Chargewise deployment is private. Ask the owner for access.",
        status_code=status.HTTP_403_FORBIDDEN,
        details={"reason": "LIVE_ACCESS_RESTRICTED"},
    )


def _enforce_demo(request: Request, db: Session) -> None:
    if db.execute(_REAL_VEHICLE_EXISTS).scalar_one():
        logger.error("demo_mode_unsafe", extra={"reason": "real_vehicle_in_demo_database"})
        raise ApiError(
            "DEMO_MODE_UNSAFE",
            "The public demo is unavailable.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    method = request.method.upper()
    if method in _READ_METHODS:
        return
    # The allowed routes have no path parameters, so the request path is exact.
    if (method, request.url.path.rstrip("/")) in DEMO_WRITE_ALLOWED:
        return
    raise ApiError(
        "FORBIDDEN",
        "The public demo is read-only. You can still plan trips.",
        status_code=status.HTTP_403_FORBIDDEN,
        details={"reason": "DEMO_READ_ONLY"},
    )


__all__ = ["DEMO_WRITE_ALLOWED", "enforce_access"]
