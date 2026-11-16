"""Business logic for the vehicles module: Smartcar Connect completion (F3).

Completion only learns which vehicles were authorized *after* the redirect,
by listing the Smartcar user's connections (smartcar-v3-notes.md §2-3). The
redirect's ``user_id`` is an unauthenticated query parameter and our
application token can read every Smartcar user's connections, so a connection
is only accepted if its ``externalId`` (which we set to the Chargewise profile
ID in the Connect URL) matches the user inside the signed ``state``. The
router has already checked that ``state`` belongs to the signed-in user.

Logging rule (arch §9): counts and outcomes only. Never Smartcar user, vehicle
or connection IDs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.integrations.smartcar.client import SmartcarClient
from app.integrations.smartcar.schemas import (
    Connection,
    normalize_signals,
    supported_signal_codes,
)
from app.modules.users.service import get_or_create_profile
from app.modules.vehicles.models import Vehicle, VehicleSpecs
from app.modules.vehicles.spec_catalog import lookup_spec

logger = logging.getLogger(__name__)

ConnectOutcome = Literal["success", "already_connected", "error"]
VehicleResult = Literal["created", "reactivated", "duplicate", "owned_elsewhere", "incomplete"]


@dataclass(frozen=True)
class ConnectResult:
    outcome: ConnectOutcome
    vehicles: tuple[VehicleResult, ...] = ()


def complete_connect(
    db: Session,
    smartcar: SmartcarClient,
    *,
    user_id: uuid.UUID,
    sc_user_id: str,
    now: datetime | None = None,
) -> ConnectResult:
    """Persist the vehicles a user just authorized. Caller commits.

    Raises ``ApiError`` if Smartcar can't be reached; nothing is written then.
    """

    connected_at = now or datetime.now(UTC)
    connections = smartcar.list_connections(sc_user_id)
    expected_external_id = str(user_id)
    accepted = [
        c
        for c in connections
        if c.is_active
        and c.user_id == sc_user_id
        and c.attributes.user.external_id == expected_external_id
    ]
    if len(accepted) != len(connections):
        logger.warning(
            "smartcar_connect_connections_skipped",
            extra={"total": len(connections), "accepted": len(accepted)},
        )
    if not accepted:
        return ConnectResult(outcome="error")

    get_or_create_profile(db, user_id=user_id)
    results = tuple(
        _upsert_vehicle(db, smartcar, user_id=user_id, connection=c, connected_at=connected_at)
        for c in accepted
    )
    db.flush()

    if any(r in ("created", "reactivated") for r in results):
        outcome: ConnectOutcome = "success"
    elif all(r == "duplicate" for r in results):
        outcome = "already_connected"
    else:
        outcome = "error"
    logger.info(
        "smartcar_connect_completed",
        # Nested: top-level ``extra`` keys can't reuse LogRecord attributes
        # (``created`` would raise KeyError and fail the request).
        extra={"outcome": outcome, "counts": {r: results.count(r) for r in set(results)}},
    )
    return ConnectResult(outcome=outcome, vehicles=results)


def _upsert_vehicle(
    db: Session,
    smartcar: SmartcarClient,
    *,
    user_id: uuid.UUID,
    connection: Connection,
    connected_at: datetime,
) -> VehicleResult:
    sc_vehicle_id = connection.vehicle_id

    # One real car belongs to one Chargewise user at a time.
    held_elsewhere = db.scalar(
        select(Vehicle.id).where(
            Vehicle.smartcar_vehicle_id == sc_vehicle_id,
            Vehicle.user_id != user_id,
            Vehicle.connection_status != "disconnected",
        )
    )
    if held_elsewhere is not None:
        return "owned_elsewhere"

    existing = db.scalar(
        select(Vehicle)
        .where(Vehicle.user_id == user_id, Vehicle.smartcar_vehicle_id == sc_vehicle_id)
        .with_for_update()
    )
    if existing is not None and existing.connection_status == "connected":
        return "duplicate"

    attrs = connection.attributes.vehicle
    if not (attrs.make and attrs.model and attrs.year):
        return "incomplete"

    supported = _read_supported_signals(smartcar, connection.user_id, sc_vehicle_id)

    if existing is not None:
        # Reconnect after disconnect (or a leftover pending row) reactivates
        # the same row (phase2_plan.md D4). Telemetry history stays attached.
        existing.smartcar_user_id = connection.user_id
        existing.smartcar_connection_id = connection.connection_id
        existing.make, existing.model, existing.year = attrs.make, attrs.model, attrs.year
        existing.connection_status = "connected"
        existing.connected_at = connected_at
        existing.disconnected_at = None
        if supported:
            existing.supported_signals = supported
        if existing.specs is None:
            existing.specs = _default_specs(attrs.make, attrs.model, attrs.year)
        return "reactivated"

    vehicle = Vehicle(
        user_id=user_id,
        smartcar_vehicle_id=sc_vehicle_id,
        smartcar_user_id=connection.user_id,
        smartcar_connection_id=connection.connection_id,
        make=attrs.make,
        model=attrs.model,
        year=attrs.year,
        connection_status="connected",
        connected_at=connected_at,
        supported_signals=supported,
        specs=_default_specs(attrs.make, attrs.model, attrs.year),
    )
    db.add(vehicle)
    return "created"


def _read_supported_signals(
    smartcar: SmartcarClient, sc_user_id: str, vehicle_id: str
) -> list[str]:
    """Best-effort signal read. Often empty right after Connect; F5/F6 recompute."""

    try:
        signals = smartcar.get_signals(sc_user_id, vehicle_id)
    except (ApiError, ValidationError):
        logger.warning("smartcar_connect_signals_unavailable")
        return []
    return supported_signal_codes(normalize_signals(signals))


def _default_specs(make: str, model: str, year: int) -> VehicleSpecs:
    entry = lookup_spec(make, model, year)
    if entry is None:
        return VehicleSpecs(usable_capacity_kwh=None, connectors=[], charging_curve=[])
    return VehicleSpecs(
        usable_capacity_kwh=entry.usable_capacity_kwh,
        connectors=list(entry.connectors),
        charging_curve=[dict(point) for point in entry.charging_curve],
    )


__all__ = ["ConnectOutcome", "ConnectResult", "complete_connect"]
