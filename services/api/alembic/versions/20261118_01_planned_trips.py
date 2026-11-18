"""planned_trips and planned_stops

Revision ID: 20261118_01
Revises: 20261117_01
Create Date: 2026-11-18

Matches docs/database-design.md sections 3.7 and 3.8 (Phase 4, F1), including
the phase4_plan.md D6 additions (``plan_status``, ``saved_at``,
``departure_time``). Enables RLS on both tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261118_01"
down_revision: str | None = "20261117_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planned_trips",
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
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("origin", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("destination", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("departure_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="planned"),
        sa.Column("plan_status", sa.Text(), nullable=False),
        sa.Column("plan_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'completed', 'abandoned')", name="planned_trips_status_enum"
        ),
        sa.CheckConstraint(
            "plan_status IN ('feasible', 'infeasible')", name="planned_trips_plan_status_enum"
        ),
    )
    op.create_index(
        "planned_trips_user_created_idx",
        "planned_trips",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "planned_trips_user_saved_idx",
        "planned_trips",
        ["user_id", sa.text("saved_at DESC"), "id"],
        postgresql_where=sa.text("saved_at IS NOT NULL"),
    )
    # Lets the vehicle → trips cascade (and the vehicle_id filter) use an index.
    op.create_index("planned_trips_vehicle_idx", "planned_trips", ["vehicle_id"])

    op.create_table(
        "planned_stops",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "trip_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("planned_trips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "station_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("charging_stations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("arrival_soc", sa.Numeric(4, 3), nullable=False),
        sa.Column("departure_soc", sa.Numeric(4, 3), nullable=False),
        sa.Column("charging_minutes", sa.Integer(), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="planned_stops_sequence_positive"),
        sa.CheckConstraint("arrival_soc BETWEEN 0 AND 1", name="planned_stops_arrival_soc_range"),
        sa.CheckConstraint(
            "departure_soc BETWEEN 0 AND 1", name="planned_stops_departure_soc_range"
        ),
        sa.CheckConstraint(
            "charging_minutes >= 0", name="planned_stops_charging_minutes_nonnegative"
        ),
        sa.UniqueConstraint("trip_id", "sequence", name="planned_stops_trip_sequence_uq"),
    )
    # Lets the station → stops SET NULL (cache eviction) use an index.
    op.create_index("planned_stops_station_idx", "planned_stops", ["station_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE public.planned_trips ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE public.planned_stops ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Dropping the tables drops their indexes, constraints and RLS settings.
    op.drop_table("planned_stops")
    op.drop_table("planned_trips")
