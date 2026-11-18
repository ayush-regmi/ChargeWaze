"""SQLAlchemy models for ``planned_trips`` and ``planned_stops``.

Mirrors ``docs/database-design.md`` sections 3.7 and 3.8 (phase4_plan.md F1,
D6). Constraint and index names match migration ``20261118_01`` so
autogenerate sees no drift.

A trip row is written once by the planner (``service.create_planned_trip``)
and never recomputed: ``plan_payload`` is the ``/routes/plan`` body as
returned. Only ``saved_at`` (and, from Phase 5, ``status``) change later.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import JSON_TYPE, UUID_TYPE

# The FKs reference these tables; importing registers them on Base.metadata.
from app.modules.stations import models as _stations_models  # noqa: F401
from app.modules.users import models as _users_models  # noqa: F401

TRIP_STATUSES = ("planned", "completed", "abandoned")
PLAN_STATUSES = ("feasible", "infeasible")


class PlannedTrip(Base):
    __tablename__ = "planned_trips"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planned', 'completed', 'abandoned')",
            name="planned_trips_status_enum",
        ),
        CheckConstraint(
            "plan_status IN ('feasible', 'infeasible')",
            name="planned_trips_plan_status_enum",
        ),
        Index("planned_trips_user_created_idx", "user_id", text("created_at DESC")),
        # Default GET /trips list: saved trips, newest saved first.
        Index(
            "planned_trips_user_saved_idx",
            "user_id",
            text("saved_at DESC"),
            "id",
            postgresql_where=text("saved_at IS NOT NULL"),
        ),
        Index("planned_trips_vehicle_idx", "vehicle_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    # {lat, lon, label}; label may be null.
    origin: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    destination: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    # Stored from the request; not used by the v1 planner.
    departure_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="planned", server_default="planned"
    )
    plan_status: Mapped[str] = mapped_column(Text, nullable=False)
    plan_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # NULL until the user saves the plan (D6). Unsaved rows expire (P6-F5).
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    stops: Mapped[list[PlannedStop]] = relationship(
        "PlannedStop",
        back_populates="trip",
        order_by="PlannedStop.sequence",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PlannedStop(Base):
    __tablename__ = "planned_stops"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="planned_stops_sequence_positive"),
        CheckConstraint("arrival_soc BETWEEN 0 AND 1", name="planned_stops_arrival_soc_range"),
        CheckConstraint("departure_soc BETWEEN 0 AND 1", name="planned_stops_departure_soc_range"),
        CheckConstraint("charging_minutes >= 0", name="planned_stops_charging_minutes_nonnegative"),
        UniqueConstraint("trip_id", "sequence", name="planned_stops_trip_sequence_uq"),
        Index("planned_stops_station_idx", "station_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, server_default=text("gen_random_uuid()")
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("planned_trips.id", ondelete="CASCADE"), nullable=False
    )
    # Becomes NULL if the station is evicted from the cache; the stop itself
    # stays in plan_payload.
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("charging_stations.id", ondelete="SET NULL"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    arrival_soc: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    departure_soc: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    # The rounded API value (D16).
    charging_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    trip: Mapped[PlannedTrip] = relationship("PlannedTrip", back_populates="stops")


__all__ = ["PLAN_STATUSES", "TRIP_STATUSES", "PlannedStop", "PlannedTrip"]
