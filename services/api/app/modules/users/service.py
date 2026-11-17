"""Business logic for the users module."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal

from fastapi import status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.users.models import Profile, UserPreferences
from app.modules.users.schemas import UserPreferencesUpdate


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
    """

    profile = db.get(Profile, user_id)
    if profile is not None:
        if profile.preferences is None:
            db.add(UserPreferences(user_id=user_id))
            db.flush()
        return profile

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


__all__ = ["clear_primary_vehicle", "get_or_create_profile", "update_preferences"]
