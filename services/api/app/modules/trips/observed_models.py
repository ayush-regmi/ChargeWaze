"""SQLAlchemy models for ``observed_trips`` and ``prediction_results``.

Mirrors ``docs/database-design.md`` sections 3.9 and 3.10 (phase5_plan.md
F1). Constraint and index names match migration ``20261120_01`` so
autogenerate sees no drift.

One ``ObservedTrip`` is one driving leg (D1), written and upserted on
``(vehicle_id, odometer_start_km)`` by trip inference (F3). Confirmed and
rejected legs are frozen (D3). A ``PredictionResult`` is either a plan score
(``kind = 'plan'``, D4) or a per-leg backtest (``kind = 'leg'``, D9).
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
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import JSON_TYPE, UUID_TYPE

# The FKs reference these tables; importing registers them on Base.metadata.
from app.modules.trips import models as _trips_models  # noqa: F401
from app.modules.users import models as _users_models  # noqa: F401
from app.modules.vehicles import models as _vehicles_models  # noqa: F401

QUALITY_FLAGS = ("high", "medium", "low")
REVIEW_STATUSES = ("auto", "needs_review", "confirmed", "rejected")
# Inference never changes or deletes a leg in one of these states (D3).
FROZEN_REVIEW_STATUSES = ("confirmed", "rejected")
ENERGY_SOURCES = ("soc_inferred", "manual")
CAPACITY_SOURCES = ("vehicle", "catalog")
PREDICTION_KINDS = ("plan", "leg")


class ObservedTrip(Base):
    __tablename__ = "observed_trips"
    __table_args__ = (
        CheckConstraint("start_time_latest >= start_time", name="observed_trips_start_bound_order"),
        CheckConstraint("end_time >= start_time_latest", name="observed_trips_end_after_start"),
        CheckConstraint(
            "odometer_end_km > odometer_start_km", name="observed_trips_odometer_increases"
        ),
        CheckConstraint("distance_km > 0", name="observed_trips_distance_positive"),
        CheckConstraint(
            "distance_km = odometer_end_km - odometer_start_km",
            name="observed_trips_distance_matches_odometer",
        ),
        CheckConstraint(
            "soc_start_percent BETWEEN 0 AND 100", name="observed_trips_soc_start_range"
        ),
        CheckConstraint("soc_end_percent BETWEEN 0 AND 100", name="observed_trips_soc_end_range"),
        CheckConstraint(
            "capacity_source IN ('vehicle', 'catalog')", name="observed_trips_capacity_source_enum"
        ),
        CheckConstraint(
            "(capacity_source IS NULL) = (usable_capacity_kwh IS NULL)",
            name="observed_trips_capacity_source_iff_capacity",
        ),
        CheckConstraint(
            "energy_source IN ('soc_inferred', 'manual')", name="observed_trips_energy_source_enum"
        ),
        CheckConstraint(
            "(energy_source IS NULL) = (energy_kwh IS NULL)",
            name="observed_trips_energy_source_iff_energy",
        ),
        CheckConstraint(
            "quality_flag IN ('high', 'medium', 'low')", name="observed_trips_quality_flag_enum"
        ),
        CheckConstraint(
            "review_status IN ('auto', 'needs_review', 'confirmed', 'rejected')",
            name="observed_trips_review_status_enum",
        ),
        UniqueConstraint(
            "vehicle_id", "odometer_start_km", name="observed_trips_vehicle_odometer_start_uq"
        ),
        Index("observed_trips_user_end_idx", "user_id", text("end_time DESC"), "id"),
        Index("observed_trips_vehicle_end_idx", "vehicle_id", text("end_time DESC")),
        Index(
            "observed_trips_planned_trip_idx",
            "planned_trip_id",
            postgresql_where=text("planned_trip_id IS NOT NULL"),
        ),
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
    # Start is a bound (D1): the car left between start_time and
    # start_time_latest. Equal when the start is exact.
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    start_time_latest: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Last reading with an odometer increase; always a real reading.
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    odometer_start_km: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    odometer_end_km: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    distance_km: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    soc_start_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    soc_start_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    soc_end_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    soc_end_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The capacity the energy was computed with (D10).
    usable_capacity_kwh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    capacity_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Observed driving energy; NULL when SoC can't give it (D1).
    energy_kwh: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    energy_kwh_uncertainty: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    energy_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_flag: Mapped[str] = mapped_column(Text, nullable=False)
    # D8 reason codes, e.g. ["soc_delta_small"].
    quality_reasons: Mapped[list[str]] = mapped_column(
        JSON_TYPE, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    review_status: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # False while the leg may still grow (D14).
    closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Set by matching (D4). The leg's index in the plan's chain isn't stored.
    planned_trip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("planned_trips.id", ondelete="SET NULL"), nullable=True
    )
    inference_version: Mapped[str] = mapped_column(Text, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # ORM-maintained; a bulk UPDATE/upsert must set it itself.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PredictionResult(Base):
    __tablename__ = "prediction_results"
    __table_args__ = (
        CheckConstraint("kind IN ('plan', 'leg')", name="prediction_results_kind_enum"),
        CheckConstraint(
            "(kind = 'plan') = (planned_trip_id IS NOT NULL)",
            name="prediction_results_plan_iff_planned_trip",
        ),
        CheckConstraint(
            "(kind = 'leg') = (observed_trip_id IS NOT NULL)",
            name="prediction_results_leg_iff_observed_trip",
        ),
        CheckConstraint(
            "predicted_arrival_soc BETWEEN 0 AND 1",
            name="prediction_results_predicted_arrival_soc_range",
        ),
        CheckConstraint(
            "observed_arrival_soc BETWEEN 0 AND 1",
            name="prediction_results_observed_arrival_soc_range",
        ),
        CheckConstraint(
            "observed_start_soc BETWEEN 0 AND 1",
            name="prediction_results_observed_start_soc_range",
        ),
        UniqueConstraint(
            "planned_trip_id", "model_version", name="prediction_results_plan_model_uq"
        ),
        UniqueConstraint(
            "observed_trip_id", "model_version", name="prediction_results_leg_model_uq"
        ),
        Index(
            "prediction_results_user_kind_created_idx",
            "user_id",
            "kind",
            text("created_at DESC"),
        ),
        Index("prediction_results_model_created_idx", "model_version", "created_at"),
        Index("prediction_results_vehicle_idx", "vehicle_id"),
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
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Set iff kind = 'plan'.
    planned_trip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("planned_trips.id", ondelete="CASCADE"), nullable=True
    )
    # Set iff kind = 'leg'. Plan rows reach their legs via
    # observed_trips.planned_trip_id.
    observed_trip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("observed_trips.id", ondelete="CASCADE"), nullable=True
    )
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    distance_km: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    observed_distance_km: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    predicted_energy_kwh: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    observed_energy_kwh: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    # observed - predicted.
    error_kwh: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    # Plan rows only (fractions 0..1).
    predicted_arrival_soc: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    observed_arrival_soc: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    observed_start_soc: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    arrival_soc_error_points: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "CAPACITY_SOURCES",
    "ENERGY_SOURCES",
    "FROZEN_REVIEW_STATUSES",
    "PREDICTION_KINDS",
    "QUALITY_FLAGS",
    "REVIEW_STATUSES",
    "ObservedTrip",
    "PredictionResult",
]
