"""Account deletion and consent records (P6-F5, PRD §4.6).

Deletion (``DELETE /api/v1/me``, api §3.1b) runs in one transaction:

0. Take the per-account advisory lock (``lock_account``), which Connect
   completion also takes, so no vehicle can be stored meanwhile without
   being revoked here.
1. Insert the ``account_deletions`` tombstone. From then on
   ``get_or_create_profile`` refuses this user, so an access token that is
   still valid can't lazily recreate the profile (``/me``, Connect, the
   planner, favorites all go through it).
2. Lock the user's vehicles and revoke their Smartcar connections
   (``vehicles.account_deletion``). A failed revocation doesn't stop the
   deletion; its connection ID is stored on the tombstone.
3. Delete the ``profiles`` row. Every user-owned table cascades from it in
   the database (db-design §6).
4. Commit, then delete the Supabase auth user. If that fails, or the admin
   API isn't configured, it stays owed on the tombstone.

``scripts/retention.py`` retries owed work (``process_deletion_backlog``) and
removes a tombstone once nothing is owed and the auth user has been gone for
longer than the longest access-token lifetime. The age counts from
``auth_user_deleted_at``, not from the request: until Supabase confirms the
deletion the user can keep refreshing tokens, so a token issued just before
it is valid for one more token lifetime.

Logging rule (arch §9): counts and outcomes only, never user or Smartcar IDs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.integrations.smartcar.client import SmartcarClient
from app.integrations.supabase.admin import AuthUserDeletion, SupabaseAdminClient
from app.modules.users.models import Profile
from app.modules.users.privacy_models import AccountDeletion, ConsentRecord
from app.modules.users.service import get_or_create_profile, lock_account
from app.modules.vehicles.account_deletion import (
    lock_and_revoke_user_vehicles,
    retry_revocations,
)

logger = logging.getLogger(__name__)

CONSENT_SOURCE = "web"


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountDeletionResult:
    profile_deleted: bool
    vehicles: int
    smartcar_revoked: int
    smartcar_pending: int
    auth_user: AuthUserDeletion | None  # None: already deleted by an earlier call


def delete_account(
    db: Session,
    smartcar: SmartcarClient,
    supabase: SupabaseAdminClient,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> AccountDeletionResult:
    """Delete the caller's account and everything it owns. Commits.

    Idempotent: calling it again (with a still-valid token) finds no profile
    and only retries the Supabase deletion if that is still owed.
    """

    current = now or datetime.now(UTC)
    # Before reading anything: Connect takes the same lock, so its vehicles
    # are either committed (and revoked below) or it waits for the tombstone.
    lock_account(db, user_id=user_id)
    db.execute(
        pg_insert(AccountDeletion)
        .values(user_id=user_id, requested_at=current)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    tombstone = db.execute(
        select(AccountDeletion)
        .where(AccountDeletion.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()

    revocations = lock_and_revoke_user_vehicles(db, smartcar, user_id=user_id)
    if revocations.failed_connection_ids:
        tombstone.pending_smartcar_connection_ids = _merge(
            tombstone.pending_smartcar_connection_ids, revocations.failed_connection_ids
        )

    deleted = db.execute(delete(Profile).where(Profile.id == user_id))
    profile_deleted = bool(getattr(deleted, "rowcount", 0))
    db.commit()

    auth_user: AuthUserDeletion | None = None
    if tombstone.auth_user_deleted_at is None:
        auth_user = supabase.delete_user(user_id)
        if auth_user in ("deleted", "already_deleted"):
            tombstone.auth_user_deleted_at = datetime.now(UTC)
            db.commit()

    pending = len(tombstone.pending_smartcar_connection_ids)
    logger.info(
        "account_deleted",
        extra={
            "profile_deleted": profile_deleted,
            "vehicles": revocations.vehicles,
            "smartcar_revoked": revocations.revoked,
            "smartcar_pending": pending,
            "auth_user": auth_user or "already_deleted",
        },
    )
    return AccountDeletionResult(
        profile_deleted=profile_deleted,
        vehicles=revocations.vehicles,
        smartcar_revoked=revocations.revoked,
        smartcar_pending=pending,
        auth_user=auth_user,
    )


@dataclass
class DeletionBacklogSummary:
    tombstones: int = 0
    smartcar_revoked: int = 0
    smartcar_pending: int = 0
    smartcar_abandoned: int = 0
    auth_users_deleted: int = 0
    auth_users_pending: int = 0
    tombstones_removed: int = 0


def process_deletion_backlog(
    db: Session,
    smartcar: SmartcarClient,
    supabase: SupabaseAdminClient,
    *,
    now: datetime,
    tombstone_days: int,
    give_up_days: int,
) -> DeletionBacklogSummary:
    """Retry owed revocations and auth-user deletions; remove finished tombstones.

    A tombstone is removed once no revocation is owed and the auth user was
    deleted more than ``tombstone_days`` ago (never on the run that deletes it).

    Flushes; the caller commits. Rows another run holds are skipped
    (``SKIP LOCKED``), so two overlapping runs never call Smartcar twice.
    """

    summary = DeletionBacklogSummary()
    tombstones = db.scalars(
        select(AccountDeletion)
        .order_by(AccountDeletion.requested_at)
        .with_for_update(skip_locked=True)
    ).all()
    for row in tombstones:
        summary.tombstones += 1
        attempted = False

        pending = list(row.pending_smartcar_connection_ids)
        if pending and row.requested_at < now - timedelta(days=give_up_days):
            # Smartcar has been failing for this long: stop and say so. The
            # vehicles are deleted, so any webhook for them is dropped anyway.
            summary.smartcar_abandoned += len(pending)
            logger.error("smartcar_revocation_abandoned", extra={"count": len(pending)})
            pending = []
        elif pending:
            attempted = True
            remaining = retry_revocations(db, smartcar, pending)
            summary.smartcar_revoked += len(pending) - len(remaining)
            pending = remaining
        if pending != row.pending_smartcar_connection_ids:
            row.pending_smartcar_connection_ids = pending
        summary.smartcar_pending += len(pending)

        if row.auth_user_deleted_at is None:
            attempted = True
            outcome = supabase.delete_user(row.user_id)
            if outcome in ("deleted", "already_deleted"):
                row.auth_user_deleted_at = now
                summary.auth_users_deleted += 1
            else:
                summary.auth_users_pending += 1

        if attempted:
            row.attempts += 1
            row.last_attempt_at = now

        # Age from the auth-user deletion: a token issued just before it
        # stays valid for one token lifetime, and the tombstone must outlive it.
        auth_gone_at = row.auth_user_deleted_at
        if (
            not pending
            and auth_gone_at is not None
            and auth_gone_at < now - timedelta(days=tombstone_days)
        ):
            db.delete(row)
            summary.tombstones_removed += 1
    db.flush()
    return summary


def count_deletion_backlog(db: Session) -> int:
    """Tombstones with work still owed (for ``--dry-run``)."""

    return int(
        db.scalar(
            select(func.count())
            .select_from(AccountDeletion)
            .where(
                or_(
                    AccountDeletion.auth_user_deleted_at.is_(None),
                    func.jsonb_array_length(AccountDeletion.pending_smartcar_connection_ids) > 0,
                )
            )
        )
        or 0
    )


def _merge(existing: list[str], new: tuple[str, ...]) -> list[str]:
    merged = list(existing)
    for connection_id in new:
        if connection_id not in merged:
            merged.append(connection_id)
    return merged


# ---------------------------------------------------------------------------
# Consent records
# ---------------------------------------------------------------------------


def latest_consent(db: Session, *, user_id: uuid.UUID, kind: str) -> ConsentRecord | None:
    return db.scalars(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id, ConsentRecord.kind == kind)
        .order_by(ConsentRecord.recorded_at.desc(), ConsentRecord.id.desc())
        .limit(1)
    ).first()


def record_consent(
    db: Session,
    *,
    user_id: uuid.UUID,
    kind: str,
    version: str,
    granted: bool,
    now: datetime | None = None,
) -> ConsentRecord:
    """Append one consent decision. Caller commits.

    Always a new row, even if it repeats the last decision: the table is the
    audit trail (PRD §4.6 "stored and auditable").
    """

    get_or_create_profile(db, user_id=user_id)
    record = ConsentRecord(
        user_id=user_id,
        kind=kind,
        version=version,
        granted=granted,
        recorded_at=now or datetime.now(UTC),
        source=CONSENT_SOURCE,
    )
    db.add(record)
    db.flush()
    return record


__all__ = [
    "AccountDeletionResult",
    "DeletionBacklogSummary",
    "count_deletion_backlog",
    "delete_account",
    "latest_consent",
    "process_deletion_backlog",
    "record_consent",
]
