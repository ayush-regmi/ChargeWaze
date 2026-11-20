"""consent_records and account_deletions

Revision ID: 20261123_01
Revises: 20261122_01
Create Date: 2026-11-19

P6-F5 account deletion and privacy (docs/database-design.md sections 3.11
and 3.12):

- ``consent_records``: append-only audit of privacy-notice acknowledgements.
  Deleted with the profile.
- ``account_deletions``: one row per deleted account, with no foreign key (the
  profile is gone). It blocks a still-valid JWT from lazily recreating the
  profile, and records the Smartcar revocations and the Supabase auth-user
  deletion still owed, which the retention job retries.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261123_01"
down_revision: str | None = "20261122_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('privacy_notice')", name="consent_records_kind_enum"),
        sa.CheckConstraint("source IN ('web')", name="consent_records_source_enum"),
        sa.CheckConstraint(
            "length(version) BETWEEN 1 AND 32", name="consent_records_version_length"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["profiles.id"],
            name="consent_records_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="consent_records_pkey"),
    )
    op.create_index(
        "consent_records_user_kind_recorded_idx",
        "consent_records",
        ["user_id", "kind", sa.text("recorded_at DESC")],
    )
    op.execute("ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY")

    op.create_table(
        "account_deletions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("auth_user_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "pending_smartcar_connection_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "jsonb_typeof(pending_smartcar_connection_ids) = 'array'",
            name="account_deletions_pending_is_array",
        ),
        sa.CheckConstraint("attempts >= 0", name="account_deletions_attempts_nonnegative"),
        sa.PrimaryKeyConstraint("user_id", name="account_deletions_pkey"),
    )
    op.execute("ALTER TABLE account_deletions ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("account_deletions")
    op.drop_index("consent_records_user_kind_recorded_idx", table_name="consent_records")
    op.drop_table("consent_records")
