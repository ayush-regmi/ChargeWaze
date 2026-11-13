"""Guards on the migrated schema itself."""

from __future__ import annotations

from sqlalchemy import Connection, text


def test_every_public_table_has_rls_enabled(db_connection: Connection) -> None:
    """Supabase exposes ``public`` through its Data API with the browser key.

    A table without RLS is readable and writable by anyone holding that key,
    so every migration that creates a table must also enable RLS.
    """

    missing = db_connection.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class c
            WHERE c.relnamespace = 'public'::regnamespace
              AND c.relkind IN ('r', 'p')
              AND NOT c.relrowsecurity
            ORDER BY c.relname
            """
        )
    ).scalars().all()
    assert missing == [], f"Tables without row level security: {missing}"
