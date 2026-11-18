"""Business logic for planned trips (Phase 4 F1, phase4_plan.md §3 F1 and §4.4).

``create_planned_trip`` is called by the planner (F4) inside its own
transaction; it flushes but never commits. The read/save/delete functions back
the ``/trips`` routes.

Every lookup is scoped to the caller: a trip that exists but belongs to someone
else is indistinguishable from one that doesn't exist (404), so IDs can't be
probed (engineering-guidelines §Security).

Pagination is keyset on ``(saved_at, id)`` for saved trips and
``(created_at, id)`` for unsaved ones: the key descending, ties by ``id``
ascending, which matches the ``planned_trips_user_saved_idx`` index. The cursor
is an opaque base64url string of ``{list, key, id}``. It isn't signed: queries
always filter on the caller, so a forged cursor can only move within the
caller's own trips.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import status
from sqlalchemy import ColumnElement, Row, and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.trips.models import PlannedStop, PlannedTrip
from app.modules.trips.schemas import TripDetail, TripListItem
from app.modules.vehicles.garage import get_vehicle_detail

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

ListMode = Literal["saved", "unsaved"]


@dataclass(frozen=True)
class TripEndpoint:
    lat: float
    lon: float
    label: str | None


@dataclass(frozen=True)
class NewPlannedStop:
    sequence: int  # 1-based
    station_id: uuid.UUID | None
    arrival_soc: float
    departure_soc: float
    charging_minutes: int


@dataclass(frozen=True)
class TripPage:
    items: list[TripListItem]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Write (called by the planner, F4)
# ---------------------------------------------------------------------------


def create_planned_trip(
    db: Session,
    *,
    trip_id: uuid.UUID,
    created_at: datetime,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    origin: TripEndpoint,
    destination: TripEndpoint,
    departure_time: datetime | None,
    plan_status: Literal["feasible", "infeasible"],
    plan_payload: Mapping[str, Any],
    model_version: str,
    stops: Sequence[NewPlannedStop],
    is_simulated: bool = False,
) -> None:
    """Store a plan (unsaved) and its stops. Flushes; the caller commits.

    ``plan_payload`` must be JSON-serializable (the §3.7 body dumped with
    ``mode="json"``); it is stored as given. ``stops`` must be in route order
    with sequences ``1..n``.
    """

    sequences = [s.sequence for s in stops]
    if sequences != list(range(1, len(stops) + 1)):
        raise ValueError(f"stop sequences must be 1..n in order, got {sequences}")

    trip = PlannedTrip(
        id=trip_id,
        created_at=created_at,
        user_id=user_id,
        vehicle_id=vehicle_id,
        origin=asdict(origin),
        destination=asdict(destination),
        departure_time=departure_time,
        status="planned",
        plan_status=plan_status,
        plan_payload=dict(plan_payload),
        model_version=model_version,
        is_simulated=is_simulated,
        saved_at=None,
    )
    trip.stops = [
        PlannedStop(
            sequence=s.sequence,
            station_id=s.station_id,
            arrival_soc=Decimal(str(s.arrival_soc)),
            departure_soc=Decimal(str(s.departure_soc)),
            charging_minutes=s.charging_minutes,
        )
        for s in stops
    ]
    db.add(trip)
    db.flush()


# ---------------------------------------------------------------------------
# Read / save / delete (the /trips routes)
# ---------------------------------------------------------------------------


def list_trips(
    db: Session,
    *,
    user_id: uuid.UUID,
    saved: bool = True,
    vehicle_id: uuid.UUID | None = None,
    trip_status: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> TripPage:
    mode: ListMode = "saved" if saved else "unsaved"
    if vehicle_id is not None:
        # NOT_FOUND unless the caller owns the vehicle (connected or not).
        get_vehicle_detail(db, user_id=user_id, vehicle_id=vehicle_id, stale_after=timedelta(0))

    key_col = PlannedTrip.saved_at if saved else PlannedTrip.created_at
    query = _item_query().where(PlannedTrip.user_id == user_id)
    query = query.where(
        PlannedTrip.saved_at.is_not(None) if saved else PlannedTrip.saved_at.is_(None)
    )
    if vehicle_id is not None:
        query = query.where(PlannedTrip.vehicle_id == vehicle_id)
    if trip_status is not None:
        query = query.where(PlannedTrip.status == trip_status)
    if cursor is not None:
        after_key, after_id = decode_cursor(cursor, mode=mode)
        query = query.where(_after(key_col, after_key, after_id))
    query = query.order_by(key_col.desc(), PlannedTrip.id.asc()).limit(limit + 1)

    rows = db.execute(query).all()
    items = [_item(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        last_key = last.saved_at if saved else last.created_at
        assert last_key is not None
        next_cursor = encode_cursor(mode=mode, key=last_key, trip_id=last.id)
    return TripPage(items=items, next_cursor=next_cursor)


def get_trip(db: Session, *, user_id: uuid.UUID, trip_id: uuid.UUID) -> TripDetail:
    trip = db.scalar(
        select(PlannedTrip).where(PlannedTrip.id == trip_id, PlannedTrip.user_id == user_id)
    )
    if trip is None:
        raise _not_found()
    return TripDetail(
        id=trip.id,
        vehicle_id=trip.vehicle_id,
        status=trip.status,  # type: ignore[arg-type]
        plan_status=trip.plan_status,  # type: ignore[arg-type]
        created_at=trip.created_at,
        saved_at=trip.saved_at,
        plan=trip.plan_payload,
        observation=None,
    )


def get_trip_item(db: Session, *, user_id: uuid.UUID, trip_id: uuid.UUID) -> TripListItem:
    row = db.execute(
        _item_query().where(PlannedTrip.id == trip_id, PlannedTrip.user_id == user_id)
    ).first()
    if row is None:
        raise _not_found()
    return _item(row)


def save_trip(db: Session, *, user_id: uuid.UUID, trip_id: uuid.UUID) -> TripListItem:
    """Set ``saved_at`` once. Saving a saved trip keeps its first ``saved_at``."""

    found = db.execute(
        update(PlannedTrip)
        .where(PlannedTrip.id == trip_id, PlannedTrip.user_id == user_id)
        .values(saved_at=func.coalesce(PlannedTrip.saved_at, func.now()))
        .returning(PlannedTrip.id)
        .execution_options(synchronize_session=False)
    ).first()
    if found is None:
        raise _not_found()
    return get_trip_item(db, user_id=user_id, trip_id=trip_id)


def delete_trip(db: Session, *, user_id: uuid.UUID, trip_id: uuid.UUID) -> None:
    """Delete a trip; its stops go with it (ON DELETE CASCADE)."""

    deleted = db.execute(
        delete(PlannedTrip)
        .where(PlannedTrip.id == trip_id, PlannedTrip.user_id == user_id)
        .returning(PlannedTrip.id)
        .execution_options(synchronize_session=False)
    ).first()
    if deleted is None:
        raise _not_found()


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def encode_cursor(*, mode: ListMode, key: datetime, trip_id: uuid.UUID) -> str:
    body = json.dumps({"m": mode, "k": key.isoformat(), "i": str(trip_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, *, mode: ListMode) -> tuple[datetime, uuid.UUID]:
    """``(key, id)`` from ``cursor``. Anything invalid is a 422 ``VALIDATION_ERROR``."""

    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise _invalid_cursor() from None

    # The payload is client-controlled JSON: check every field's type before
    # using it, so a wrong type is a 422 and never an unexpected exception.
    if not isinstance(data, dict):
        raise _invalid_cursor()
    raw_mode, raw_key, raw_id = data.get("m"), data.get("k"), data.get("i")
    if not (isinstance(raw_mode, str) and isinstance(raw_key, str) and isinstance(raw_id, str)):
        raise _invalid_cursor()
    if raw_mode != mode:
        raise _invalid_cursor()  # a cursor from the other list
    try:
        key = datetime.fromisoformat(raw_key)
        trip_id = uuid.UUID(raw_id)
    except ValueError:
        raise _invalid_cursor() from None
    if key.tzinfo is None:
        raise _invalid_cursor()
    return key, trip_id


def _invalid_cursor() -> ApiError:
    return ApiError(
        "VALIDATION_ERROR",
        "Invalid cursor; list the trips again without it.",
        status_code=422,
        details={"reason": "INVALID_CURSOR"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item_query() -> Any:
    stop_count = (
        select(func.count(PlannedStop.id))
        .where(PlannedStop.trip_id == PlannedTrip.id)
        .scalar_subquery()
    )
    return select(
        PlannedTrip.id,
        PlannedTrip.vehicle_id,
        PlannedTrip.origin,
        PlannedTrip.destination,
        PlannedTrip.status,
        PlannedTrip.plan_status,
        PlannedTrip.plan_payload["route"]["distance_km"].label("distance_km"),
        PlannedTrip.plan_payload["totals"]["total_minutes"].label("total_minutes"),
        stop_count.label("stop_count"),
        PlannedTrip.created_at,
        PlannedTrip.saved_at,
    )


def _after(key_col: Any, key: datetime, trip_id: uuid.UUID) -> ColumnElement[bool]:
    """Rows after ``(key, id)`` in ``key DESC, id ASC`` order."""

    return or_(key_col < key, and_(key_col == key, PlannedTrip.id > trip_id))


def _item(row: Row[Any]) -> TripListItem:
    return TripListItem(
        id=row.id,
        vehicle_id=row.vehicle_id,
        origin_label=_label(row.origin),
        destination_label=_label(row.destination),
        status=row.status,
        plan_status=row.plan_status,
        distance_km=_number(row.distance_km),
        total_minutes=_whole(row.total_minutes),
        stop_count=row.stop_count,
        created_at=row.created_at,
        saved_at=row.saved_at,
    )


def _label(endpoint: Any) -> str | None:
    label = endpoint.get("label") if isinstance(endpoint, dict) else None
    return label if isinstance(label, str) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _whole(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _not_found() -> ApiError:
    return ApiError("NOT_FOUND", "Trip not found.", status_code=status.HTTP_404_NOT_FOUND)


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "NewPlannedStop",
    "TripEndpoint",
    "TripPage",
    "create_planned_trip",
    "decode_cursor",
    "delete_trip",
    "encode_cursor",
    "get_trip",
    "get_trip_item",
    "list_trips",
    "save_trip",
]
