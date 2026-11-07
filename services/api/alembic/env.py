"""Alembic environment.

Reads the connection URL from ``Settings.database_url`` so tests, local dev,
and CI all share one source of truth. Limits autogenerate to the ``public``
schema so we never accidentally touch Supabase's ``auth`` schema.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.db.session import Base

# Import every module's models so Base.metadata is complete.
from app.modules.users import models as _users_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to) -> bool:  # type: ignore[no-untyped-def]
    """Skip Supabase-managed schemas on autogenerate."""

    is_other_schema = type_ == "table" and getattr(object, "schema", None) not in (
        None,
        "public",
    )
    return not is_other_schema


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
        include_schemas=False,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
            include_schemas=False,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
