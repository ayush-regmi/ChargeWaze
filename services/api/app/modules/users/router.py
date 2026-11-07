"""HTTP routes for the users module (``/api/v1/me``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.users.schemas import MeResponse, UserPreferencesOut
from app.modules.users.service import get_or_create_profile

router = APIRouter(tags=["users"])


@router.get("/me", response_model=MeResponse)
def read_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Return the authenticated caller's profile and preferences.

    Lazily creates the ``profiles`` and ``user_preferences`` rows on the
    first call. Matches ``docs/api-contracts.md`` section 3.1.
    """

    display_name = None
    user_metadata = current_user.raw_claims.get("user_metadata")
    if isinstance(user_metadata, dict):
        candidate = user_metadata.get("full_name") or user_metadata.get("name")
        if isinstance(candidate, str):
            display_name = candidate

    profile = get_or_create_profile(
        db, user_id=current_user.user_id, display_name=display_name
    )
    db.commit()
    db.refresh(profile)
    assert profile.preferences is not None  # seeded by the service above

    return MeResponse(
        id=profile.id,
        display_name=profile.display_name,
        email=current_user.email,
        created_at=profile.created_at,
        preferences=UserPreferencesOut.model_validate(profile.preferences),
    )


__all__ = ["router"]
