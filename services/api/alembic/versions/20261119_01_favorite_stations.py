"""favorite_stations

Revision ID: 20261119_01
Revises: 20261118_01
Create Date: 2026-11-19

Matches docs/database-design.md section 3.6a (phase3_plan.md F8). Enables RLS.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261119_01"
down_revision: str | None = "20261118_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "favorite_stations",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "station_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("charging_stations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Lets the station → favorites cascade (cache eviction) use an index.
    op.create_index("favorite_stations_station_idx", "favorite_stations", ["station_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE public.favorite_stations ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Dropping the table drops its index, constraints and RLS setting.
    op.drop_table("favorite_stations")
