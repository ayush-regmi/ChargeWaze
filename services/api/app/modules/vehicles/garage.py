"""Business logic for the Garage: list, detail and disconnect (Phase 2 F4).

Every lookup is scoped to the caller: a vehicle that exists but belongs to
someone else is indistinguishable from one that doesn't exist (404), so IDs
can't be probed (engineering-guidelines §Security).

Disconnect is "local disconnect wins" (phase2_plan.md F4): the row is marked
``disconnected`` even if Smartcar can't be reached, so the webhook ingestor
stops accepting its telemetry (E3). ``smartcar_connection_id`` is cleared only
once Smartcar confirms the revocation; a disconnected row that still has one is
a revocation still owed, which a later retry job can pick up.

Logging rule (arch §9): outcomes only, never Smartcar IDs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ApiError
from app.integrations.smartcar.client import SmartcarClient
from app.modules.vehicles.latest_state import is_stale, last_recorded_at
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.schemas import (
    ChargingCurvePoint,
    VehicleDetail,
    VehicleSpecsOut,
    VehicleSummary,
)

logger = logging.getLogger(__name__)

RevocationOutcome = Literal["revoked", "already_revoked", "failed", "not_needed"]


def list_vehicles(
    db: Session,
    *,
    user_id: uuid.UUID,
    include_disconnected: bool,
    stale_after: timedelta,
    now: datetime | None = None,
) -> list[VehicleSummary]:
    query = select(Vehicle).where(Vehicle.user_id == user_id)
    if not include_disconnected:
        query = query.where(Vehicle.connection_status == "connected")
    # Most recently connected first; id breaks ties so the order is stable.
    query = query.order_by(Vehicle.connected_at.desc().nulls_last(), Vehicle.id)
    current = now or datetime.now(UTC)
    return [_summary(v, stale_after=stale_after, now=current) for v in db.scalars(query).all()]


def get_vehicle_detail(
    db: Session,
    *,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    stale_after: timedelta,
    now: datetime | None = None,
) -> VehicleDetail:
    vehicle = db.scalar(
        select(Vehicle)
        .options(selectinload(Vehicle.specs))
        .where(Vehicle.id == vehicle_id, Vehicle.user_id == user_id)
    )
    if vehicle is None:
        raise _not_found()
    summary = _summary(vehicle, stale_after=stale_after, now=now or datetime.now(UTC))
    specs = vehicle.specs
    return VehicleDetail(
        **summary.model_dump(),
        connected_at=vehicle.connected_at,
        disconnected_at=vehicle.disconnected_at,
        specs=None
        if specs is None
        else VehicleSpecsOut(
            usable_capacity_kwh=None
            if specs.usable_capacity_kwh is None
            else float(specs.usable_capacity_kwh),
            connectors=list(specs.connectors),
            charging_curve=[ChargingCurvePoint.model_validate(p) for p in specs.charging_curve],
        ),
    )


def disconnect_vehicle(
    db: Session,
    smartcar: SmartcarClient,
    *,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    now: datetime | None = None,
) -> RevocationOutcome:
    """Mark the vehicle disconnected and revoke its Smartcar connection. Caller commits.

    Idempotent: an already-disconnected vehicle is left as is, and Smartcar is
    not called again.
    """

    vehicle = db.scalar(
        select(Vehicle)
        .where(Vehicle.id == vehicle_id, Vehicle.user_id == user_id)
        .with_for_update()
    )
    if vehicle is None:
        raise _not_found()
    if vehicle.connection_status == "disconnected":
        return "not_needed"

    # Local state first: telemetry must stop being accepted whatever Smartcar does.
    vehicle.connection_status = "disconnected"
    vehicle.disconnected_at = now or datetime.now(UTC)

    outcome = _revoke(smartcar, vehicle)
    if outcome in ("revoked", "already_revoked"):
        vehicle.smartcar_connection_id = None
    db.flush()
    logger.info("vehicle_disconnected", extra={"revocation": outcome})
    return outcome


def _revoke(smartcar: SmartcarClient, vehicle: Vehicle) -> RevocationOutcome:
    # Simulated vehicles have no Smartcar connection.
    if not vehicle.smartcar_connection_id:
        return "not_needed"
    try:
        smartcar.delete_connection(vehicle.smartcar_connection_id)
    except ApiError as exc:
        if (exc.details or {}).get("upstream_status") == status.HTTP_404_NOT_FOUND:
            # Already removed on Smartcar's side (e.g. revoked from their portal).
            return "already_revoked"
        logger.warning("smartcar_revoke_failed", extra={"code": exc.code})
        return "failed"
    except ValidationError:
        # Smartcar answered the token request with something unexpected.
        logger.warning("smartcar_revoke_failed", extra={"code": "INVALID_RESPONSE"})
        return "failed"
    return "revoked"


def _summary(vehicle: Vehicle, *, stale_after: timedelta, now: datetime) -> VehicleSummary:
    recorded_at = last_recorded_at(vehicle.latest_state)
    return VehicleSummary(
        id=vehicle.id,
        make=vehicle.make,
        model=vehicle.model,
        year=vehicle.year,
        connection_status=vehicle.connection_status,  # type: ignore[arg-type]
        supported_signals=list(vehicle.supported_signals),
        last_state_recorded_at=recorded_at,
        is_stale=is_stale(recorded_at, stale_after=stale_after, now=now),
    )


def _not_found() -> ApiError:
    return ApiError("NOT_FOUND", "Vehicle not found.", status_code=status.HTTP_404_NOT_FOUND)


__all__ = ["RevocationOutcome", "disconnect_vehicle", "get_vehicle_detail", "list_vehicles"]
