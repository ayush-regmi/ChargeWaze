"""On-demand vehicle refresh: one live Smartcar read to fill a webhook gap (ADR 0010).

Webhooks are the primary telemetry path (ADR 0002). When they were missed
(API or tunnel down), the user can press "Refresh"; this module makes one
``GET /v3/vehicles/{id}/signals`` call and stores the result through the same
ingest code as a webhook delivery.

Rules:

- Only the vehicle's owner, only for a connected, real (non-simulated) car.
- Per-vehicle rate limit, kept in ``vehicle_refreshes`` so it holds across
  API instances: one refresh per ``min_interval``, at most ``max_per_day`` in
  any rolling 24 hours. An attempt counts once it is about to call Smartcar,
  whether or not Smartcar answers, so a failing upstream isn't hammered.
- The vehicle row is locked (``FOR UPDATE``) while the limit is checked and the
  attempt recorded; concurrent clicks serialize and the second is refused.
  No lock is held during the Smartcar call.

``GET /vehicles/{id}/state`` never comes here: reads stay cache-only.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import status
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.integrations.smartcar import errors as smartcar_errors
from app.integrations.smartcar.client import SmartcarClient
from app.modules.vehicles.models import Vehicle, VehicleRefresh
from app.modules.vehicles.schemas import VehicleState
from app.modules.vehicles.telemetry_service import IngestResult, ingest_polled_signals
from app.modules.vehicles.vehicle_state import build_vehicle_state

logger = logging.getLogger(__name__)

RATE_WINDOW = timedelta(hours=24)
# Refresh log rows older than this are deleted when a new attempt is recorded.
REFRESH_LOG_RETENTION = timedelta(days=30)


@dataclass(frozen=True)
class RefreshLimits:
    min_interval: timedelta
    max_per_day: int


@dataclass(frozen=True)
class RefreshOutcome:
    refreshed_at: datetime
    inserted: int
    next_refresh_at: datetime
    state: VehicleState
    # For scheduling the post-ingest hooks (ADR 0009).
    ingest: IngestResult


def refresh_vehicle(
    db: Session,
    smartcar: SmartcarClient,
    *,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    limits: RefreshLimits,
    stale_after: timedelta,
    now: datetime | None = None,
) -> RefreshOutcome:
    """Refresh one vehicle from Smartcar. Raises ``ApiError`` for every refusal."""

    now = now or datetime.now(UTC)

    # 1. Lock, authorize, rate-limit, record the attempt.
    vehicle = db.execute(
        select(Vehicle)
        .where(Vehicle.id == vehicle_id, Vehicle.user_id == user_id)
        .with_for_update()
    ).scalar_one_or_none()
    if vehicle is None:
        db.rollback()
        raise ApiError("NOT_FOUND", "Vehicle not found.", status_code=status.HTTP_404_NOT_FOUND)
    sc_user_id = vehicle.smartcar_user_id
    sc_vehicle_id = vehicle.smartcar_vehicle_id
    if (
        vehicle.connection_status != "connected"
        or vehicle.is_simulated
        or not sc_user_id
        or not sc_vehicle_id
    ):
        db.rollback()
        raise _not_connected()

    recent = list(
        db.scalars(
            select(VehicleRefresh.requested_at)
            .where(
                VehicleRefresh.vehicle_id == vehicle.id,
                VehicleRefresh.requested_at > now - RATE_WINDOW,
            )
            .order_by(VehicleRefresh.requested_at)
        )
    )
    allowed_at = next_allowed_at(recent, limits=limits)
    if allowed_at is not None and allowed_at > now:
        db.rollback()
        raise _rate_limited(allowed_at, now)

    db.execute(
        delete(VehicleRefresh).where(
            VehicleRefresh.vehicle_id == vehicle.id,
            VehicleRefresh.requested_at < now - REFRESH_LOG_RETENTION,
        )
    )
    attempt = VehicleRefresh(vehicle_id=vehicle.id, requested_at=now, outcome="pending")
    db.add(attempt)
    db.commit()
    attempt_id = attempt.id
    next_refresh_at = next_allowed_at([*recent, now], limits=limits) or now

    # 2. One Smartcar read, no lock held.
    try:
        signals = smartcar.get_signals(sc_user_id, sc_vehicle_id)
    except ApiError as exc:
        if exc.code == "SMARTCAR_NOT_CONFIGURED":
            # Never reached Smartcar: don't spend the user's allowance on it.
            _forget(db, attempt_id)
        else:
            upstream = (exc.details or {}).get("upstream_status")
            _finish(db, attempt_id, outcome="failed", upstream_status=_int_or_none(upstream))
        logger.warning("smartcar_refresh_failed", extra={"code": exc.code})
        raise
    except ValidationError:
        _finish(db, attempt_id, outcome="failed")
        logger.warning("smartcar_refresh_failed", extra={"code": "unparseable_signals"})
        raise ApiError(
            smartcar_errors.UPSTREAM_ERROR,
            "Smartcar returned an unexpected response.",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from None

    # 3. Store through the webhook's ingest rules.
    result = ingest_polled_signals(db, vehicle_id=vehicle_id, signals=signals, now=now)
    if not result.accepted:
        # Disconnected while Smartcar was answering.
        _finish(db, attempt_id, outcome="failed")
        raise _not_connected()
    _finish(db, attempt_id, outcome="ok", inserted=result.inserted)

    refreshed = db.get(Vehicle, vehicle_id)
    if refreshed is None:  # deleted with its profile meanwhile
        raise ApiError("NOT_FOUND", "Vehicle not found.", status_code=status.HTTP_404_NOT_FOUND)
    db.refresh(refreshed)
    state = build_vehicle_state(
        refreshed.id,
        refreshed.latest_state,
        is_simulated=refreshed.is_simulated,
        stale_after=stale_after,
        now=now,
    )
    return RefreshOutcome(
        refreshed_at=now,
        inserted=result.inserted,
        next_refresh_at=next_refresh_at,
        state=state,
        ingest=result,
    )


def next_allowed_at(attempts: list[datetime], *, limits: RefreshLimits) -> datetime | None:
    """When the next refresh is allowed, given attempts in the last 24 h (sorted).

    ``None`` when there are no attempts. The answer may be in the past.
    """

    if not attempts:
        return None
    by_interval = attempts[-1] + limits.min_interval
    if len(attempts) < limits.max_per_day:
        return by_interval
    # The window frees a slot when the oldest attempt that still counts expires.
    by_daily_cap = attempts[-limits.max_per_day] + RATE_WINDOW
    return max(by_interval, by_daily_cap)


def _finish(
    db: Session,
    attempt_id: uuid.UUID,
    *,
    outcome: str,
    inserted: int = 0,
    upstream_status: int | None = None,
) -> None:
    attempt = db.get(VehicleRefresh, attempt_id)
    if attempt is not None:
        attempt.outcome = outcome
        attempt.inserted = inserted
        attempt.upstream_status = upstream_status
    db.commit()


def _forget(db: Session, attempt_id: uuid.UUID) -> None:
    db.execute(delete(VehicleRefresh).where(VehicleRefresh.id == attempt_id))
    db.commit()


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _not_connected() -> ApiError:
    return ApiError(
        "VEHICLE_NOT_CONNECTED",
        "Only a connected vehicle can be refreshed.",
        status_code=status.HTTP_409_CONFLICT,
    )


def _rate_limited(allowed_at: datetime, now: datetime) -> ApiError:
    seconds = max(1, math.ceil((allowed_at - now).total_seconds()))
    return ApiError(
        "RATE_LIMITED",
        "This vehicle was refreshed recently; try again later.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        details={
            "retry_after_seconds": seconds,
            "next_refresh_at": allowed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        },
        headers={"Retry-After": str(seconds)},
    )


__all__ = [
    "RATE_WINDOW",
    "REFRESH_LOG_RETENTION",
    "RefreshLimits",
    "RefreshOutcome",
    "next_allowed_at",
    "refresh_vehicle",
]
