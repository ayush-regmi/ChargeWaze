"""SQLAlchemy models for ``vehicles``, ``vehicle_specs`` and ``vehicle_telemetry``.

Mirrors ``docs/database-design.md`` sections 3.3 to 3.5. Constraint and index
names match migration ``20261113_01`` so autogenerate sees no drift.
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import JSON_TYPE, UUID_TYPE

CONNECTION_STATUSES = ("connected", "disconnected", "pending")
TELEMETRY_SOURCES = ("smartcar", "manual", "simulated")


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint(
            "connection_status IN ('connected', 'disconnected', 'pending')",
            name="vehicles_connection_status_enum",
        ),
        # One row per real car per user. Reconnecting reactivates this row
        # instead of inserting a new one (phase2_plan.md D4).
        Index(
            "vehicles_user_smartcar_vehicle_uq",
            "user_id",
            "smartcar_vehicle_id",
            unique=True,
            postgresql_where=text("smartcar_vehicle_id IS NOT NULL"),
        ),
        Index("vehicles_user_status_idx", "user_id", "connection_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable for simulated vehicles.
    smartcar_vehicle_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    smartcar_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Smartcar V3 connection, needed for DELETE /v3/connections/{id} on
    # disconnect (smartcar-v3-notes.md, D1).
    smartcar_connection_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    make: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    connection_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )
    supported_signals: Mapped[list[str]] = mapped_column(
        JSON_TYPE, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # NULL until the first webhook arrives ("Awaiting first update").
    latest_state: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE, nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    specs: Mapped[VehicleSpecs | None] = relationship(
        "VehicleSpecs",
        back_populates="vehicle",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class VehicleSpecs(Base):
    __tablename__ = "vehicle_specs"
    __table_args__ = (
        CheckConstraint("usable_capacity_kwh > 0", name="vehicle_specs_usable_capacity_positive"),
        CheckConstraint(
            "baseline_kwh_per_km > 0 AND baseline_kwh_per_km <= 1",
            name="vehicle_specs_baseline_range",
        ),
    )

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True
    )
    # NULL when the catalog has no entry and the user hasn't filled it in
    # (phase2_plan.md D8). Never guessed.
    usable_capacity_kwh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    connectors: Mapped[list[str]] = mapped_column(
        JSON_TYPE, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    charging_curve: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_TYPE, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # User-entered consumption (phase4_plan.md F8). NULL means "use the catalog
    # value"; never copied from the catalog, so a value here is the user's own.
    baseline_kwh_per_km: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)

    vehicle: Mapped[Vehicle] = relationship("Vehicle", back_populates="specs")


class VehicleTelemetry(Base):
    """Append-only log of every signal reading. Never updated in place."""

    __tablename__ = "vehicle_telemetry"
    __table_args__ = (
        CheckConstraint(
            "source IN ('smartcar', 'manual', 'simulated')",
            name="vehicle_telemetry_source_enum",
        ),
        # Webhook dedup at signal granularity: a redelivered event inserts nothing.
        Index(
            "vehicle_telemetry_source_event_signal_uq",
            "source",
            "source_event_id",
            "signal_code",
            unique=True,
            postgresql_where=text("source_event_id IS NOT NULL"),
        ),
        Index(
            "vehicle_telemetry_vehicle_signal_recorded_idx",
            "vehicle_id",
            "signal_code",
            text("recorded_at DESC"),
        ),
        Index(
            "vehicle_telemetry_vehicle_recorded_idx",
            "vehicle_id",
            text("recorded_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, server_default=text("gen_random_uuid()")
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    # Provider timestamp (Smartcar oemUpdatedAt).
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_code: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL when the signal carries no numeric value (e.g. an unknown signal kept
    # only in raw_payload). Booleans are stored as 0/1.
    value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


__all__ = [
    "CONNECTION_STATUSES",
    "TELEMETRY_SOURCES",
    "Vehicle",
    "VehicleSpecs",
    "VehicleTelemetry",
]
