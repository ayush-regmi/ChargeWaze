"""vehicle_specs.baseline_kwh_per_km

Revision ID: 20261121_01
Revises: 20261120_01
Create Date: 2026-11-19

Matches docs/database-design.md section 3.4 (phase4_plan.md F8). Nullable, no
backfill: NULL means "use the catalog value" (phase4_plan.md D14).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261121_01"
down_revision: str | None = "20261120_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vehicle_specs",
        sa.Column("baseline_kwh_per_km", sa.Numeric(5, 3), nullable=True),
    )
    op.create_check_constraint(
        "vehicle_specs_baseline_range",
        "vehicle_specs",
        "baseline_kwh_per_km > 0 AND baseline_kwh_per_km <= 1",
    )


def downgrade() -> None:
    op.drop_constraint("vehicle_specs_baseline_range", "vehicle_specs", type_="check")
    op.drop_column("vehicle_specs", "baseline_kwh_per_km")
