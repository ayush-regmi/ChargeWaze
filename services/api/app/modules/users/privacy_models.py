"""SQLAlchemy models for ``consent_records`` and ``account_deletions`` (P6-F5).

Mirrors ``docs/database-design.md`` sections 3.11 and 3.12 and migration
``20261123_01``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import JSON_TYPE, UUID_TYPE

# Registers ``profiles`` on the metadata for the foreign key below.
from app.modules.users import models as _users_models  # noqa: F401

CONSENT_KINDS = ("privacy_notice",)
CONSENT_SOURCES = ("web",)


class ConsentRecord(Base):
    """One consent decision. Append-only: a change is a new row, never an update."""

    __tablename__ = "consent_records"
    __table_args__ = (
        CheckConstraint("kind IN ('privacy_notice')", name="consent_records_kind_enum"),
        CheckConstraint("source IN ('web')", name="consent_records_source_enum"),
        CheckConstraint("length(version) BETWEEN 1 AND 32", name="consent_records_version_length"),
        Index(
            "consent_records_user_kind_recorded_idx",
            "user_id",
            "kind",
            text("recorded_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("profiles.id", ondelete="CASCADE", name="consent_records_user_id_fkey"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)


class AccountDeletion(Base):
    """Tombstone for a deleted account. No foreign key: the profile is gone.

    Holds the Supabase user ID (a random UUID that links to nothing once the
    auth user is deleted) and the work still owed to third parties. The
    retention job retries that work and removes the row once it's done and
    older than any access token could be.
    """

    __tablename__ = "account_deletions"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(pending_smartcar_connection_ids) = 'array'",
            name="account_deletions_pending_is_array",
        ),
        CheckConstraint("attempts >= 0", name="account_deletions_attempts_nonnegative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    auth_user_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pending_smartcar_connection_ids: Mapped[list[str]] = mapped_column(
        JSON_TYPE, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["CONSENT_KINDS", "CONSENT_SOURCES", "AccountDeletion", "ConsentRecord"]
