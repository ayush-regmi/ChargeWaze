"""Column types shared by every module's models."""

from __future__ import annotations

from sqlalchemy import JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB

# JSONB on Postgres. The JSON fallback only matters for non-Postgres engines;
# the test suite runs on Postgres (see tests/conftest.py).
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
# Native uuid on Postgres, 32-char string on SQLite.
UUID_TYPE = Uuid(as_uuid=True)

__all__ = ["JSON_TYPE", "UUID_TYPE"]
