"""vehicles, vehicle_specs, vehicle_telemetry

Revision ID: 20261113_01
Revises: 20261112_01
Create Date: 2026-11-13

Matches docs/database-design.md sections 3.3 to 3.5 (Phase 2, F1). Also adds
the user_preferences.primary_vehicle_id foreign key (phase2_plan.md D6) and
enables RLS on every new table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261113_01"
down_revision: str | None = "20261112_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = ("vehicles", "vehicle_specs", "vehicle_telemetry")
EMPTY_JSONB_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("smartcar_vehicle_id", sa.Text(), nullable=True),
        sa.Column("smartcar_user_id", sa.Text(), nullable=True),
        sa.Column("smartcar_connection_id", sa.Text(), nullable=True),
        sa.Column("make", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("connection_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "supported_signals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_ARRAY,
        ),
        sa.Column("latest_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint(
            "connection_status IN ('connected', 'disconnected', 'pending')",
            name="vehicles_connection_status_enum",
        ),
    )
    op.create_index(
        "vehicles_user_smartcar_vehicle_uq",
        "vehicles",
        ["user_id", "smartcar_vehicle_id"],
        unique=True,
        postgresql_where=sa.text("smartcar_vehicle_id IS NOT NULL"),
    )
    op.create_index("vehicles_user_status_idx", "vehicles", ["user_id", "connection_status"])

    op.create_table(
        "vehicle_specs",
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("usable_capacity_kwh", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "connectors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_ARRAY,
        ),
        sa.Column(
            "charging_curve",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_ARRAY,
        ),
        sa.CheckConstraint(
            "usable_capacity_kwh > 0", name="vehicle_specs_usable_capacity_positive"
        ),
    )

    op.create_table(
        "vehicle_telemetry",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=True),
        sa.Column("signal_code", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint(
            "source IN ('smartcar', 'manual', 'simulated')",
            name="vehicle_telemetry_source_enum",
        ),
    )
    op.create_index(
        "vehicle_telemetry_source_event_signal_uq",
        "vehicle_telemetry",
        ["source", "source_event_id", "signal_code"],
        unique=True,
        postgresql_where=sa.text("source_event_id IS NOT NULL"),
    )
    op.create_index(
        "vehicle_telemetry_vehicle_signal_recorded_idx",
        "vehicle_telemetry",
        ["vehicle_id", "signal_code", sa.text("recorded_at DESC")],
    )
    op.create_index(
        "vehicle_telemetry_vehicle_recorded_idx",
        "vehicle_telemetry",
        ["vehicle_id", sa.text("recorded_at DESC")],
    )

    # No vehicles existed before this revision, so any stored value is dangling
    # and would block the FK. The column has never been writable through the API.
    op.execute(
        "UPDATE user_preferences SET primary_vehicle_id = NULL "
        "WHERE primary_vehicle_id IS NOT NULL"
    )
    op.create_foreign_key(
        "user_preferences_primary_vehicle_id_fkey",
        "user_preferences",
        "vehicles",
        ["primary_vehicle_id"],
        ["id"],
        ondelete="SET NULL",
    )

    if op.get_bind().dialect.name == "postgresql":
        for table in NEW_TABLES:
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_constraint(
        "user_preferences_primary_vehicle_id_fkey", "user_preferences", type_="foreignkey"
    )
    # Dropping a table drops its indexes and RLS setting with it.
    op.drop_table("vehicle_telemetry")
    op.drop_table("vehicle_specs")
    op.drop_table("vehicles")
