"""Retention deletes for the vehicles module's tables (P6-F5, db-design §6).

Called by ``scripts/retention.py``. Each function deletes and flushes; the
script commits, so a large purge is split into short transactions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.modules.vehicles.models import Vehicle, VehicleRefresh, VehicleTelemetry


def vehicle_ids(db: Session) -> list[uuid.UUID]:
    return list(db.scalars(select(Vehicle.id).order_by(Vehicle.id)).all())


def count_old_telemetry(db: Session, *, older_than: datetime) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(VehicleTelemetry)
            .where(VehicleTelemetry.recorded_at < older_than)
        )
        or 0
    )


def purge_old_telemetry_batch(
    db: Session, *, vehicle_id: uuid.UUID, older_than: datetime, batch_size: int
) -> int:
    """Delete up to ``batch_size`` of one vehicle's readings recorded before ``older_than``.

    Per vehicle so the ``(vehicle_id, recorded_at DESC)`` index finds the rows.
    ``recorded_at`` is the provider's timestamp, so a late delivery of an old
    reading is purged on the next run. Returns the number deleted; the caller
    repeats until it is below ``batch_size``.
    """

    batch = (
        select(VehicleTelemetry.id)
        .where(
            VehicleTelemetry.vehicle_id == vehicle_id,
            VehicleTelemetry.recorded_at < older_than,
        )
        .limit(batch_size)
        .scalar_subquery()
    )
    result = db.execute(delete(VehicleTelemetry).where(VehicleTelemetry.id.in_(batch)))
    return _rowcount(result)


def count_old_refreshes(db: Session, *, older_than: datetime) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(VehicleRefresh)
            .where(VehicleRefresh.requested_at < older_than)
        )
        or 0
    )


def purge_old_refreshes(db: Session, *, older_than: datetime) -> int:
    """Delete refresh-log rows older than ``older_than``.

    Refresh already prunes a vehicle's log when it records a new attempt; this
    covers vehicles that are never refreshed again.
    """

    result = db.execute(delete(VehicleRefresh).where(VehicleRefresh.requested_at < older_than))
    return _rowcount(result)


def _rowcount(result: object) -> int:
    return result.rowcount if isinstance(result, CursorResult) else 0


__all__ = [
    "count_old_refreshes",
    "count_old_telemetry",
    "purge_old_refreshes",
    "purge_old_telemetry_batch",
    "vehicle_ids",
]
