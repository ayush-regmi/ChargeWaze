"""enable row level security on public tables

Revision ID: 20261112_01
Revises: 20261106_01
Create Date: 2026-11-12

Supabase exposes the public schema through its Data API. With RLS enabled and
no policies, the anon and authenticated roles cannot read or write these
tables; FastAPI connects as the table owner, which bypasses RLS.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261112_01"
down_revision: str | None = "20261106_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("profiles", "user_preferences", "alembic_version")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
