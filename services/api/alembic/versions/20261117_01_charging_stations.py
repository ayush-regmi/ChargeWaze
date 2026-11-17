"""charging_stations

Revision ID: 20261117_01
Revises: 20261113_01
Create Date: 2026-11-17

Matches docs/database-design.md section 3.6 (Phase 3, F1): the spec columns
plus the phase3_plan.md D8 additions. Enables RLS on the new table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261117_01"
down_revision: str | None = "20261113_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "charging_stations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("operator", sa.Text(), nullable=True),
        sa.Column("network", sa.Text(), nullable=True),
        sa.Column("street_address", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lon", sa.Numeric(9, 6), nullable=False),
        sa.Column(
            "connectors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("access", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("access_detail", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("pricing_text", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "provider IN ('afdc', 'simulated')", name="charging_stations_provider_enum"
        ),
        sa.CheckConstraint(
            "access IN ('public', 'private', 'unknown')", name="charging_stations_access_enum"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'planned', 'temporarily_unavailable', 'unknown')",
            name="charging_stations_status_enum",
        ),
        sa.CheckConstraint("country ~ '^[A-Z]{2}$'", name="charging_stations_country_iso2"),
        sa.CheckConstraint("lat BETWEEN -90 AND 90", name="charging_stations_lat_range"),
        sa.CheckConstraint("lon BETWEEN -180 AND 180", name="charging_stations_lon_range"),
        sa.CheckConstraint(
            "is_simulated = (provider = 'simulated')",
            name="charging_stations_simulated_matches_provider",
        ),
        sa.UniqueConstraint(
            "provider", "external_id", name="charging_stations_provider_external_id_uq"
        ),
    )
    op.create_index("charging_stations_lat_lon_idx", "charging_stations", ["lat", "lon"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE public.charging_stations ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Dropping the table drops its indexes, constraints and RLS setting with it.
    op.drop_table("charging_stations")
