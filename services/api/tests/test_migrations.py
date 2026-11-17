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


def test_models_match_migrated_schema(db_connection: Connection) -> None:
    """Models and migrations must describe the same schema.

    Catches a column, index or constraint that was added to one but not the other.
    """

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import app.modules.stations.models
    import app.modules.users.models  # noqa: F401  (also registers the vehicle models)
    from app.db.session import Base

    context = MigrationContext.configure(
        db_connection, opts={"compare_type": True, "compare_server_default": False}
    )
    diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"Schema drift between models and migrations: {diff}"
