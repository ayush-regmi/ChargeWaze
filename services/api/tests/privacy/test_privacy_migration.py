"""Migration ``20261123_01``: consent_records and account_deletions (P6-F5)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text

from alembic import command

API_ROOT = Path(__file__).resolve().parents[2]
THIS_REVISION = "20261123_01"
PREVIOUS_REVISION = "20261122_01"
TABLES = ("consent_records", "account_deletions")


def _alembic_config(conn: Connection | None = None) -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    if conn is not None:
        cfg.attributes["connection"] = conn
    return cfg


def _public_tables(conn: Connection) -> set[str]:
    return set(
        conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars()
    )


def test_one_head_and_this_revision_follows_vehicle_refreshes() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1
    chain = {rev.revision for rev in script.walk_revisions("base", heads[0])}
    assert THIS_REVISION in chain
    revision = script.get_revision(THIS_REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION


def test_both_tables_have_rls_enabled(db_connection: Connection) -> None:
    enabled = dict(
        db_connection.execute(
            text(
                "SELECT relname, relrowsecurity FROM pg_class "
                "WHERE relname = ANY(:names) AND relkind = 'r'"
            ),
            {"names": list(TABLES)},
        ).all()
    )
    assert enabled == dict.fromkeys(TABLES, True)


def test_migration_downgrades_and_upgrades_cleanly(db_connection: Connection) -> None:
    # Runs inside db_connection's outer transaction, so the rollback restores it.
    cfg = _alembic_config(db_connection)
    command.downgrade(cfg, PREVIOUS_REVISION)
    assert _public_tables(db_connection).isdisjoint(TABLES)
    command.upgrade(cfg, "head")
    assert _public_tables(db_connection).issuperset(TABLES)
