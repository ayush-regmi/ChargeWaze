"""SQLAlchemy model for ``charging_stations``.

Mirrors ``docs/database-design.md`` section 3.6 (phase3_plan.md F1, D7 and D8).
Constraint and index names match migration ``20261117_01`` so autogenerate sees
no drift.

The table is a global cache of provider data, not the source of truth: searches
always go to the provider and upsert here on ``(provider, external_id)``, which
keeps each station's Chargewise ``id`` stable across fetches.
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

# F0 settled on AFDC only (it includes NRCan's Canadian data), so no 'ocm'/'nrcan'.
STATION_PROVIDERS = ("afdc", "simulated")
STATION_ACCESS = ("public", "private", "unknown")
STATION_STATUSES = ("open", "planned", "temporarily_unavailable", "unknown")


class ChargingStation(Base):
    __tablename__ = "charging_stations"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('afdc', 'simulated')",
            name="charging_stations_provider_enum",
        ),
        CheckConstraint(
            "access IN ('public', 'private', 'unknown')",
            name="charging_stations_access_enum",
        ),
        CheckConstraint(
            "status IN ('open', 'planned', 'temporarily_unavailable', 'unknown')",
            name="charging_stations_status_enum",
        ),
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="charging_stations_country_iso2"),
        CheckConstraint("lat BETWEEN -90 AND 90", name="charging_stations_lat_range"),
        CheckConstraint("lon BETWEEN -180 AND 180", name="charging_stations_lon_range"),
        # Simulated rows are always tagged, and only simulated rows are.
        CheckConstraint(
            "is_simulated = (provider = 'simulated')",
            name="charging_stations_simulated_matches_provider",
        ),
        # Upsert target: the same provider station always maps to the same row.
        UniqueConstraint(
            "provider", "external_id", name="charging_stations_provider_external_id_uq"
        ),
        Index("charging_stations_lat_lon_idx", "lat", "lon"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, server_default=text("gen_random_uuid()")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable fields stay NULL when the provider doesn't say (PRD §7): never guessed.
    operator: Mapped[str | None] = mapped_column(Text, nullable=True)
    network: Mapped[str | None] = mapped_column(Text, nullable=True)
    street_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ISO 3166-1 alpha-2, e.g. 'US', 'CA'.
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    lon: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    # [{type, count, power_kw}]; count and power_kw are null when unknown (D9).
    connectors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_TYPE, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    access: Mapped[str] = mapped_column(
        Text, nullable=False, default="unknown", server_default="unknown"
    )
    # Provider's access restriction detail (AFDC access_detail_code), shown as-is.
    access_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="unknown", server_default="unknown"
    )
    # Provider pricing text, shown as-is and never parsed (D8).
    pricing_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provider's own last-updated timestamp.
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When we last fetched this row from the provider (TTL refresh, D7).
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["STATION_ACCESS", "STATION_PROVIDERS", "STATION_STATUSES", "ChargingStation"]
