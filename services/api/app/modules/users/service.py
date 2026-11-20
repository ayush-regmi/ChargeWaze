"""Business logic for the users module."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal

from fastapi import status
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.users.models import Profile, UserPreferences
from app.modules.users.privacy_models import AccountDeletion
from app.modules.users.schemas import UserPreferencesUpdate

# First key of the two-key advisory lock (trips uses 5003 for vehicles), so
# account locks can't collide with other advisory locks.
_ACCOUNT_LOCK_NAMESPACE = 5004


def lock_account(db: Session, *, user_id: uuid.UUID) -> None:
    """Serialize account deletion with Connect for one user until the transaction ends.

    ``DELETE /me`` and Connect completion both take it before reading
    anything, so either the vehicles Connect stores are committed before the
    deletion locks and revokes them, or Connect waits, sees the committed
    tombstone and revokes what it just authorized. A row lock can't do this:
    the profile may not exist yet, and new vehicle rows aren't visible to the
    deletion's ``FOR UPDATE``. Waits rather than skipping.
    """

    db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:user_id))"),
        {"ns": _ACCOUNT_LOCK_NAMESPACE, "user_id": str(user_id)},
    )


def is_account_deleted(db: Session, *, user_id: uuid.UUID) -> bool:
    """True if ``user_id`` has an ``account_deletions`` tombstone (P6-F5)."""

    return db.get(AccountDeletion, user_id) is not None


def get_or_create_profile(
    db: Session,
    *,
    user_id: uuid.UUID,
    display_name: str | None = None,
) -> Profile:
    """Return the caller's ``profiles`` row, creating it lazily on first visit.

    Supabase owns identity; Chargewise records ``profiles.id`` equal to the
    Supabase ``auth.users.id`` on the first authenticated request from a given
    user. Default ``user_preferences`` are seeded in the same transaction.

    A deleted account (``account_deletions`` tombstone, P6-F5) is never
    recreated: its access token can stay valid until it expires, so this
    answers ``401`` instead.
    """

    profile = db.get(Profile, user_id)
    if profile is not None:
        if profile.preferences is None:
            db.add(UserPreferences(user_id=user_id))
            db.flush()
        return profile

    if is_account_deleted(db, user_id=user_id):
        raise ApiError(
            "UNAUTHENTICATED",
            "This account has been deleted.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    profile = Profile(id=user_id, display_name=display_name)
    db.add(profile)
    db.add(UserPreferences(user_id=user_id))
    db.flush()
    return profile


def update_preferences(
    db: Session,
    *,
    user_id: uuid.UUID,
    changes: UserPreferencesUpdate,
    is_selectable_vehicle: Callable[[uuid.UUID], bool],
) -> UserPreferences:
    """Apply a partial preferences update (api §3.1a). Caller commits.

    ``is_selectable_vehicle`` answers "is this one of the caller's connected
    vehicles?". It is injected because vehicles live in another module and
    this module must not query its tables (architecture §3).
    """

    get_or_create_profile(db, user_id=user_id)
    # Look the row up directly: when get_or_create_profile just seeded it, the
    # already-loaded ``profile.preferences`` relationship still says None.
    prefs = db.get(UserPreferences, user_id)
    assert prefs is not None  # seeded above

    fields = changes.model_dump(exclude_unset=True)

    if "primary_vehicle_id" in fields:
        vehicle_id = fields["primary_vehicle_id"]
        if vehicle_id is not None and not is_selectable_vehicle(vehicle_id):
            raise ApiError("NOT_FOUND", "Vehicle not found.", status_code=status.HTTP_404_NOT_FOUND)

    reserve = fields.get("reserve_soc", prefs.reserve_soc)
    charge_to = fields.get("preferred_charge_to", prefs.preferred_charge_to)
    if Decimal(str(reserve)) >= Decimal(str(charge_to)):
        raise ApiError(
            "VALIDATION_ERROR",
            "reserve_soc must be lower than preferred_charge_to.",
            status_code=422,
            details={"fields": ["reserve_soc", "preferred_charge_to"]},
        )

    for key, value in fields.items():
        setattr(prefs, key, value)
    db.flush()
    db.refresh(prefs)
    return prefs


def clear_primary_vehicle(db: Session, *, user_id: uuid.UUID, vehicle_id: uuid.UUID) -> None:
    """Unset ``primary_vehicle_id`` if it points at ``vehicle_id``. Caller commits.

    Called by the vehicles module when a vehicle is disconnected, so a
    disconnected car never stays the primary (api §3.6).
    """

    db.execute(
        update(UserPreferences)
        .where(
            UserPreferences.user_id == user_id,
            UserPreferences.primary_vehicle_id == vehicle_id,
        )
        .values(primary_vehicle_id=None)
    )


__all__ = [
    "clear_primary_vehicle",
    "get_or_create_profile",
    "is_account_deleted",
    "lock_account",
    "update_preferences",
]
