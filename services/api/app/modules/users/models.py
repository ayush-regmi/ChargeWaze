"""SQLAlchemy models for ``profiles`` and ``user_preferences``.

Mirrors ``docs/database-design.md`` sections 3.1 and 3.2. The ``profiles.id``
is a plain UUID equal to the Supabase ``auth.users.id``; we do not add a
cross-schema foreign key because migrations run against a plain Postgres in CI.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import JSON_TYPE, UUID_TYPE

# ``user_preferences.primary_vehicle_id`` references ``vehicles.id``. Loading the
# vehicle models here registers that table on ``Base.metadata`` wherever these
# models are used, or SQLAlchemy raises NoReferencedTableError at flush time.
from app.modules.vehicles import models as _vehicles_models  # noqa: F401


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    preferences: Mapped[UserPreferences | None] = relationship(
        "UserPreferences",
        back_populates="profile",
        uselist=False,
        cascade="all, delete-orphan",
    )


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint("reserve_soc BETWEEN 0 AND 1", name="user_preferences_reserve_soc_range"),
        CheckConstraint(
            "preferred_charge_to BETWEEN 0 AND 1",
            name="user_preferences_preferred_charge_to_range",
        ),
        CheckConstraint(
            "units IN ('imperial', 'metric')",
            name="user_preferences_units_enum",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    units: Mapped[str] = mapped_column(String(16), nullable=False, default="imperial")
    reserve_soc: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.15)
    preferred_charge_to: Mapped[float] = mapped_column(
        Numeric(4, 3), nullable=False, default=0.80
    )
    max_detour_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    preferred_networks: Mapped[list[str]] = mapped_column(
        JSON_TYPE, nullable=False, default=list, server_default="[]"
    )
    # FK into the vehicles module (added in migration 20261113_01). SET NULL so
    # deleting a vehicle row never deletes the user's preferences.
    primary_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey(
            "vehicles.id",
            ondelete="SET NULL",
            name="user_preferences_primary_vehicle_id_fkey",
        ),
        nullable=True,
    )

    profile: Mapped[Profile] = relationship("Profile", back_populates="preferences")


__all__ = ["Profile", "UserPreferences"]
