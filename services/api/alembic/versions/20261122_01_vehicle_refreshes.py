"""vehicle_refreshes

Revision ID: 20261122_01
Revises: 20261121_01
Create Date: 2026-11-19

Log of on-demand Smartcar reads, used for the per-vehicle refresh rate limit
(ADR 0010, docs/database-design.md section 3.5a).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261122_01"
down_revision: str | None = "20261121_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicle_refreshes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.Text(), server_default="pending", nullable=False),
        sa.Column("inserted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("upstream_status", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('pending', 'ok', 'failed')", name="vehicle_refreshes_outcome_enum"
        ),
        sa.CheckConstraint("inserted >= 0", name="vehicle_refreshes_inserted_nonnegative"),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name="vehicle_refreshes_vehicle_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="vehicle_refreshes_pkey"),
    )
    op.create_index(
        "vehicle_refreshes_vehicle_requested_idx",
        "vehicle_refreshes",
        ["vehicle_id", sa.text("requested_at DESC")],
    )
    # Same RLS posture as every other user-data table: enabled with no
    # policies, so only the API's server role can read it.
    op.execute("ALTER TABLE vehicle_refreshes ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("vehicle_refreshes_vehicle_requested_idx", table_name="vehicle_refreshes")
    op.drop_table("vehicle_refreshes")
