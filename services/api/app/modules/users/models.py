"""SQLAlchemy models for ``profiles`` and ``user_preferences``.

Mirrors ``docs/database-design.md`` sections 3.1 and 3.2. The ``profiles.id``
is a plain UUID equal to the Supabase ``auth.users.id``; we do not add a
cross-schema foreign key because migrations run against a plain Postgres in CI.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.session import Base

# JSONB on Postgres, portable JSON on SQLite (used by tests when no Postgres
# container is available). Postgres production migrations still land JSONB.
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
# Native uuid on Postgres, 32-char string on SQLite.
UUID_TYPE = Uuid(as_uuid=True)


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
    primary_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True)

    profile: Mapped[Profile] = relationship("Profile", back_populates="preferences")


__all__ = ["Profile", "UserPreferences"]
