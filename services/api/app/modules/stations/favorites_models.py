"""SQLAlchemy model for ``favorite_stations``.

Mirrors ``docs/database-design.md`` section 3.6a (phase3_plan.md F8). Index
names match migration ``20261119_01`` so autogenerate sees no drift.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import UUID_TYPE

# The FKs reference these tables; importing registers them on Base.metadata.
from app.modules.stations import models as _stations_models  # noqa: F401
from app.modules.users import models as _users_models  # noqa: F401


class FavoriteStation(Base):
    __tablename__ = "favorite_stations"
    __table_args__ = (Index("favorite_stations_station_idx", "station_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("charging_stations.id", ondelete="CASCADE"), primary_key=True
    )
    # Returned as ``favorited_at``; the list is newest first.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["FavoriteStation"]
