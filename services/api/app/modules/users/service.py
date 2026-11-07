"""Business logic for the users module."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.modules.users.models import Profile, UserPreferences


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


__all__ = ["get_or_create_profile"]
