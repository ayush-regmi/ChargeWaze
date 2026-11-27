"""Live allowlist and the public demo account (P6-F2, ADR 0012, architecture.md §7.2).

``get_current_user`` calls :func:`enforce_access` after it has verified the
token, so every user route is gated without router changes. Webhooks and
``/health`` don't authenticate a user and aren't gated.

**Demo account** (``DEMO_USER_ID``): one shared, seeded account
(``scripts/seed_demo.py``) whose credentials are public, so it is read-only
except for planning. That also stops it connecting a car (``POST
/vehicles/connect``) or refreshing one. It lives in the same database as real
accounts; every query is scoped to the caller, so it only ever sees its own
simulated rows. If the demo account itself ever owns a real
(``is_simulated = false``) vehicle, its requests are refused before a row is
read.

**Everyone else**: only the Supabase user IDs in ``LIVE_ALLOWED_USER_IDS`` get
in. An empty list means no restriction in development and test, and nobody
under ``APP_ENV=production`` (fails closed).
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
_DEMO_HAS_REAL_VEHICLE = text(
    "SELECT EXISTS (SELECT 1 FROM vehicles WHERE user_id = :user_id AND NOT is_simulated)"
)


def enforce_access(
    request: Request,
    *,
    user_id: uuid.UUID,
    settings: Settings,
    db: Session,
) -> None:
    """Raise unless ``user_id`` may make this request."""

    if is_demo_account(user_id, settings):
        _enforce_demo(request, user_id, db)
    else:
        _enforce_live(user_id, settings)


def is_demo_account(user_id: uuid.UUID, settings: Settings) -> bool:
    """Whether ``user_id`` is the shared public demo account."""

    return settings.demo_user_id is not None and user_id == settings.demo_user_id


def _enforce_live(user_id: uuid.UUID, settings: Settings) -> None:
    allowlist = settings.live_allowlist
    if not allowlist and settings.app_env != "production":
        return
    if user_id in allowlist:
        return
    raise ApiError(
        "FORBIDDEN",
        "This ChargeWaze deployment is private. Ask the owner for access.",
        status_code=status.HTTP_403_FORBIDDEN,
        details={"reason": "LIVE_ACCESS_RESTRICTED"},
    )


def _enforce_demo(request: Request, user_id: uuid.UUID, db: Session) -> None:
    if db.execute(_DEMO_HAS_REAL_VEHICLE, {"user_id": user_id}).scalar_one():
        logger.error("demo_mode_unsafe", extra={"reason": "real_vehicle_on_demo_account"})
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


__all__ = ["DEMO_WRITE_ALLOWED", "enforce_access", "is_demo_account"]
