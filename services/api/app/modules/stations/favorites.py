"""Favorite stations: schemas and service (phase3_plan.md F8, api-contracts §3.19).

Favorites are user-owned rows in ``favorite_stations``. Every query is scoped
to the caller's ``user_id``; there is no way to read or change another user's
favorites. ``PUT`` and ``DELETE`` are idempotent. The list reads the
``charging_stations`` cache only and never calls the provider.

Logging rule (D13): counts only, never station IDs with coordinates.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.integrations.stations.base import ConnectorType
from app.modules.stations.favorites_models import FavoriteStation
from app.modules.stations.models import ChargingStation
from app.modules.stations.schemas import StationOut
from app.modules.stations.service import _compatible, _from_row
from app.modules.users.models import Profile
from app.modules.users.service import get_or_create_profile

# Bounds the list response without a cursor (api-contracts §3.19a).
MAX_FAVORITES_PER_USER = 500


class FavoriteStationOut(StationOut):
    favorited_at: datetime


class FavoriteStationListResponse(BaseModel):
    items: list[FavoriteStationOut]


def list_favorites(
    db: Session,
    *,
    user_id: uuid.UUID,
    ttl: timedelta,
    vehicle_connectors: frozenset[ConnectorType] | None = None,
    now: datetime | None = None,
) -> list[FavoriteStationOut]:
    """The caller's favorites, newest first, straight from the cache."""

    current = now or datetime.now(UTC)
    rows = db.execute(
        select(ChargingStation, FavoriteStation.created_at)
        .join(FavoriteStation, FavoriteStation.station_id == ChargingStation.id)
        .where(FavoriteStation.user_id == user_id)
        .order_by(FavoriteStation.created_at.desc(), ChargingStation.id)
    ).all()
    items: list[FavoriteStationOut] = []
    for row, favorited_at in rows:
        station = _from_row(row, is_stale=current - row.fetched_at > ttl)
        items.append(
            FavoriteStationOut(
                **station.model_dump(exclude={"compatible"}),
                compatible=_compatible(station.connectors, vehicle_connectors),
                favorited_at=favorited_at,
            )
        )
    return items


def add_favorite(db: Session, *, user_id: uuid.UUID, station_id: uuid.UUID) -> None:
    """Favorite a station. A no-op if it's already a favorite."""

    _require_station(db, station_id)
    if _is_favorite(db, user_id, station_id):
        return
    # The FK needs a profile; it's created lazily on a user's first request.
    get_or_create_profile(db, user_id=user_id)
    # Serialize this user's PUTs until commit: without the lock, two PUTs for
    # different stations at 499 favorites both count 499 and both insert.
    _lock_user(db, user_id)
    # Re-check under the lock: a concurrent PUT may have added this station.
    if _is_favorite(db, user_id, station_id):
        return
    count = db.scalar(
        select(func.count()).select_from(FavoriteStation).where(FavoriteStation.user_id == user_id)
    )
    if (count or 0) >= MAX_FAVORITES_PER_USER:
        raise ApiError(
            "FAVORITES_LIMIT",
            f"You can have at most {MAX_FAVORITES_PER_USER} favorite stations.",
            status_code=status.HTTP_409_CONFLICT,
            details={"limit": MAX_FAVORITES_PER_USER},
        )
    # ON CONFLICT covers a concurrent PUT for the same station.
    db.execute(
        pg_insert(FavoriteStation)
        .values(user_id=user_id, station_id=station_id)
        .on_conflict_do_nothing(index_elements=["user_id", "station_id"])
    )


def remove_favorite(db: Session, *, user_id: uuid.UUID, station_id: uuid.UUID) -> None:
    """Unfavorite a station. A no-op if it isn't a favorite."""

    _require_station(db, station_id)
    db.execute(
        delete(FavoriteStation).where(
            FavoriteStation.user_id == user_id, FavoriteStation.station_id == station_id
        )
    )


def _lock_user(db: Session, user_id: uuid.UUID) -> None:
    """Row-lock the caller's profile; held until the transaction ends."""

    db.execute(select(Profile.id).where(Profile.id == user_id).with_for_update())


def _is_favorite(db: Session, user_id: uuid.UUID, station_id: uuid.UUID) -> bool:
    # A query, not ``db.get``: the identity map could hold a pre-lock answer.
    found = db.scalar(
        select(FavoriteStation.station_id).where(
            FavoriteStation.user_id == user_id, FavoriteStation.station_id == station_id
        )
    )
    return found is not None


def _require_station(db: Session, station_id: uuid.UUID) -> None:
    if db.get(ChargingStation, station_id) is None:
        raise ApiError("NOT_FOUND", "Station not found.", status_code=status.HTTP_404_NOT_FOUND)


__all__ = [
    "MAX_FAVORITES_PER_USER",
    "FavoriteStationListResponse",
    "FavoriteStationOut",
    "add_favorite",
    "list_favorites",
    "remove_favorite",
]
