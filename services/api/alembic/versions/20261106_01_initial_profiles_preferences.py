"""initial: profiles and user_preferences

Revision ID: 20261106_01
Revises:
Create Date: 2026-11-06

Matches docs/database-design.md sections 3.1 and 3.2.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261106_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "user_preferences",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("units", sa.String(length=16), nullable=False, server_default="imperial"),
        sa.Column("reserve_soc", sa.Numeric(4, 3), nullable=False, server_default="0.150"),
        sa.Column(
            "preferred_charge_to",
            sa.Numeric(4, 3),
            nullable=False,
            server_default="0.800",
        ),
        sa.Column(
            "max_detour_minutes", sa.Integer(), nullable=False, server_default="20"
        ),
        sa.Column(
            "preferred_networks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("primary_vehicle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "reserve_soc BETWEEN 0 AND 1", name="user_preferences_reserve_soc_range"
        ),
        sa.CheckConstraint(
            "preferred_charge_to BETWEEN 0 AND 1",
            name="user_preferences_preferred_charge_to_range",
        ),
        sa.CheckConstraint(
            "units IN ('imperial', 'metric')", name="user_preferences_units_enum"
        ),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.drop_table("profiles")
