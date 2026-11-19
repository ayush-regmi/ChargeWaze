"""observed_trips and prediction_results

Revision ID: 20261120_01
Revises: 20261119_01
Create Date: 2026-11-19

Matches docs/database-design.md sections 3.9 and 3.10 (phase5_plan.md F1).
One ``observed_trips`` row is one driving leg (D1); ``prediction_results``
holds plan scores (``kind = 'plan'``, D4) and per-leg backtests
(``kind = 'leg'``, D9). Enables RLS on both tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261120_01"
down_revision: str | None = "20261119_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _fk(name: str, target: str, *, ondelete: str, nullable: bool) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
    )


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "observed_trips",
        _uuid_pk(),
        _fk("user_id", "profiles.id", ondelete="CASCADE", nullable=False),
        _fk("vehicle_id", "vehicles.id", ondelete="CASCADE", nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_time_latest", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("odometer_start_km", sa.Numeric(10, 2), nullable=False),
        sa.Column("odometer_end_km", sa.Numeric(10, 2), nullable=False),
        sa.Column("distance_km", sa.Numeric(8, 2), nullable=False),
        sa.Column("soc_start_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("soc_start_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("soc_end_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("soc_end_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usable_capacity_kwh", sa.Numeric(6, 2), nullable=True),
        sa.Column("capacity_source", sa.Text(), nullable=True),
        sa.Column("energy_kwh", sa.Numeric(8, 3), nullable=True),
        sa.Column("energy_kwh_uncertainty", sa.Numeric(6, 3), nullable=True),
        sa.Column("energy_source", sa.Text(), nullable=True),
        sa.Column("quality_flag", sa.Text(), nullable=False),
        sa.Column(
            "quality_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("review_status", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _fk("planned_trip_id", "planned_trips.id", ondelete="SET NULL", nullable=True),
        sa.Column("inference_version", sa.Text(), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_timestamps(),
        sa.CheckConstraint(
            "start_time_latest >= start_time", name="observed_trips_start_bound_order"
        ),
        sa.CheckConstraint("end_time >= start_time_latest", name="observed_trips_end_after_start"),
        sa.CheckConstraint(
            "odometer_end_km > odometer_start_km", name="observed_trips_odometer_increases"
        ),
        sa.CheckConstraint("distance_km > 0", name="observed_trips_distance_positive"),
        sa.CheckConstraint(
            "distance_km = odometer_end_km - odometer_start_km",
            name="observed_trips_distance_matches_odometer",
        ),
        sa.CheckConstraint(
            "soc_start_percent BETWEEN 0 AND 100", name="observed_trips_soc_start_range"
        ),
        sa.CheckConstraint(
            "soc_end_percent BETWEEN 0 AND 100", name="observed_trips_soc_end_range"
        ),
        sa.CheckConstraint(
            "capacity_source IN ('vehicle', 'catalog')", name="observed_trips_capacity_source_enum"
        ),
        sa.CheckConstraint(
            "(capacity_source IS NULL) = (usable_capacity_kwh IS NULL)",
            name="observed_trips_capacity_source_iff_capacity",
        ),
        sa.CheckConstraint(
            "energy_source IN ('soc_inferred', 'manual')", name="observed_trips_energy_source_enum"
        ),
        sa.CheckConstraint(
            "(energy_source IS NULL) = (energy_kwh IS NULL)",
            name="observed_trips_energy_source_iff_energy",
        ),
        sa.CheckConstraint(
            "quality_flag IN ('high', 'medium', 'low')", name="observed_trips_quality_flag_enum"
        ),
        sa.CheckConstraint(
            "review_status IN ('auto', 'needs_review', 'confirmed', 'rejected')",
            name="observed_trips_review_status_enum",
        ),
        # The upsert key (D14): later runs extend an open leg in place.
        sa.UniqueConstraint(
            "vehicle_id", "odometer_start_km", name="observed_trips_vehicle_odometer_start_uq"
        ),
    )
    op.create_index(
        "observed_trips_user_end_idx",
        "observed_trips",
        ["user_id", sa.text("end_time DESC"), "id"],
    )
    op.create_index(
        "observed_trips_vehicle_end_idx",
        "observed_trips",
        ["vehicle_id", sa.text("end_time DESC")],
    )
    # Matching lookups, and the plan → legs SET NULL on plan delete.
    op.create_index(
        "observed_trips_planned_trip_idx",
        "observed_trips",
        ["planned_trip_id"],
        postgresql_where=sa.text("planned_trip_id IS NOT NULL"),
    )

    op.create_table(
        "prediction_results",
        _uuid_pk(),
        _fk("user_id", "profiles.id", ondelete="CASCADE", nullable=False),
        _fk("vehicle_id", "vehicles.id", ondelete="CASCADE", nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        _fk("planned_trip_id", "planned_trips.id", ondelete="CASCADE", nullable=True),
        _fk("observed_trip_id", "observed_trips.id", ondelete="CASCADE", nullable=True),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("distance_km", sa.Numeric(8, 2), nullable=False),
        sa.Column("observed_distance_km", sa.Numeric(8, 2), nullable=False),
        sa.Column("predicted_energy_kwh", sa.Numeric(8, 3), nullable=False),
        sa.Column("observed_energy_kwh", sa.Numeric(8, 3), nullable=True),
        sa.Column("error_kwh", sa.Numeric(8, 3), nullable=True),
        sa.Column("predicted_arrival_soc", sa.Numeric(4, 3), nullable=True),
        sa.Column("observed_arrival_soc", sa.Numeric(4, 3), nullable=True),
        sa.Column("observed_start_soc", sa.Numeric(4, 3), nullable=True),
        sa.Column("arrival_soc_error_points", sa.Numeric(5, 2), nullable=True),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('plan', 'leg')", name="prediction_results_kind_enum"),
        sa.CheckConstraint(
            "(kind = 'plan') = (planned_trip_id IS NOT NULL)",
            name="prediction_results_plan_iff_planned_trip",
        ),
        sa.CheckConstraint(
            "(kind = 'leg') = (observed_trip_id IS NOT NULL)",
            name="prediction_results_leg_iff_observed_trip",
        ),
        sa.CheckConstraint(
            "predicted_arrival_soc BETWEEN 0 AND 1",
            name="prediction_results_predicted_arrival_soc_range",
        ),
        sa.CheckConstraint(
            "observed_arrival_soc BETWEEN 0 AND 1",
            name="prediction_results_observed_arrival_soc_range",
        ),
        sa.CheckConstraint(
            "observed_start_soc BETWEEN 0 AND 1",
            name="prediction_results_observed_start_soc_range",
        ),
        # One score per plan (or leg) per model version. Plain (not partial)
        # unique constraints: the kind ↔ FK checks make them equivalent to
        # "WHERE kind = ...", NULLs never collide, ON CONFLICT needs no
        # index predicate, and the FK cascades can use them.
        sa.UniqueConstraint(
            "planned_trip_id", "model_version", name="prediction_results_plan_model_uq"
        ),
        sa.UniqueConstraint(
            "observed_trip_id", "model_version", name="prediction_results_leg_model_uq"
        ),
    )
    op.create_index(
        "prediction_results_user_kind_created_idx",
        "prediction_results",
        ["user_id", "kind", sa.text("created_at DESC")],
    )
    op.create_index(
        "prediction_results_model_created_idx",
        "prediction_results",
        ["model_version", "created_at"],
    )
    # Lets the vehicle → predictions cascade use an index.
    op.create_index("prediction_results_vehicle_idx", "prediction_results", ["vehicle_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE public.observed_trips ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE public.prediction_results ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Dropping the tables drops their indexes, constraints and RLS settings.
    op.drop_table("prediction_results")
    op.drop_table("observed_trips")
