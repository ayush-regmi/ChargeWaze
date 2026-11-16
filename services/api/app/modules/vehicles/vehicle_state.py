"""``GET /api/v1/vehicles/{id}/state``: the Dashboard's cached state (Phase 2 F6).

Reads **only** ``vehicles.latest_state`` (written by the webhook ingestor, F5).
It never calls Smartcar, so a page reload is served from the cache (E2).

``latest_state`` keeps raw provider units (db §4). This module converts them to
the API's unit-in-the-name fields (``range_km``, ``charging_power_kw``, …). A
value in a unit it doesn't recognise is returned as ``null`` rather than
guessed (PRD §7: missing data is shown as missing).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.vehicles.latest_state import is_stale, last_recorded_at, parse_datetime
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.schemas import VehicleState, VehicleStateSignals

KM_PER_MILE = 1.609344

_DISTANCE_TO_KM: dict[str, float] = {
    "km": 1.0,
    "kilometers": 1.0,
    "kilometres": 1.0,
    "mi": KM_PER_MILE,
    "mile": KM_PER_MILE,
    "miles": KM_PER_MILE,
}
_POWER_TO_KW: dict[str, float] = {
    "w": 0.001,
    "watt": 0.001,
    "watts": 0.001,
    "kw": 1.0,
    "kilowatt": 1.0,
    "kilowatts": 1.0,
}


def get_vehicle_state(
    db: Session,
    *,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    stale_after: timedelta,
    now: datetime | None = None,
) -> VehicleState:
    """The caller's vehicle state. ``NOT_FOUND`` if it isn't theirs."""

    vehicle = db.scalar(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == user_id))
    if vehicle is None:
        raise ApiError("NOT_FOUND", "Vehicle not found.", status_code=status.HTTP_404_NOT_FOUND)
    return build_vehicle_state(
        vehicle.id,
        vehicle.latest_state,
        is_simulated=vehicle.is_simulated,
        stale_after=stale_after,
        now=now or datetime.now(UTC),
    )


def build_vehicle_state(
    vehicle_id: uuid.UUID,
    latest_state: Mapping[str, Any] | None,
    *,
    is_simulated: bool,
    stale_after: timedelta,
    now: datetime,
) -> VehicleState:
    """Pure mapping from the cached ``latest_state`` document to the API response."""

    cached: Mapping[str, Any] = {}
    if isinstance(latest_state, Mapping) and isinstance(latest_state.get("signals"), Mapping):
        cached = latest_state["signals"]

    values: dict[str, Any] = {}
    recorded: dict[str, datetime] = {}

    def put(field: str, code: str, read: Callable[[Mapping[str, Any]], Any]) -> None:
        entry = cached.get(code)
        if not isinstance(entry, Mapping):
            return
        value = read(entry)
        if value is None:
            return
        values[field] = value
        ts = parse_datetime(entry.get("recorded_at"))
        if ts is not None:
            recorded[field] = ts

    put("soc_percent", "soc", _percent)
    put("range_km", "range", lambda e: _convert(e, _DISTANCE_TO_KM))
    put("odometer_km", "odometer", lambda e: _convert(e, _DISTANCE_TO_KM))
    put("is_charging", "charging_status", _bool)
    put("charging_power_kw", "charging_power", lambda e: _convert(e, _POWER_TO_KW))

    recorded_at = last_recorded_at(latest_state)
    has_data = recorded_at is not None
    return VehicleState(
        vehicle_id=vehicle_id,
        recorded_at=recorded_at,
        received_at=parse_datetime(latest_state.get("received_at"))
        if isinstance(latest_state, Mapping)
        else None,
        source=("simulated" if is_simulated else "smartcar") if has_data else None,
        is_stale=is_stale(recorded_at, stale_after=stale_after, now=now),
        signals=VehicleStateSignals(**values),
        signal_recorded_at=recorded,
    )


def _number(value: Any) -> float | None:
    # bool is an int subclass; a boolean is never a valid quantity.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _percent(entry: Mapping[str, Any]) -> float | None:
    unit = entry.get("unit")
    if unit is not None and str(unit).lower() != "percent":
        return None
    return _number(entry.get("value"))


def _bool(entry: Mapping[str, Any]) -> bool | None:
    value = entry.get("value")
    return value if isinstance(value, bool) else None


def _convert(entry: Mapping[str, Any], factors: Mapping[str, float]) -> float | None:
    value = _number(entry.get("value"))
    unit = entry.get("unit")
    if value is None or not isinstance(unit, str):
        return None
    factor = factors.get(unit.strip().lower())
    if factor is None:
        return None
    return round(value * factor, 3)


__all__ = ["build_vehicle_state", "get_vehicle_state"]
