"""HTTP routes for the users module (``/api/v1/me``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorEnvelope
from app.db.session import get_db
from app.integrations.smartcar.client import SmartcarClient, get_smartcar_client
from app.integrations.supabase.admin import SupabaseAdminClient, get_supabase_admin
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.users.privacy import delete_account, latest_consent, record_consent
from app.modules.users.privacy_models import CONSENT_KINDS
from app.modules.users.schemas import (
    ConsentCreate,
    ConsentStatus,
    MeResponse,
    PrivacyResponse,
    RetentionOut,
    UserPreferencesOut,
    UserPreferencesUpdate,
)
from app.modules.users.service import get_or_create_profile, update_preferences
from app.modules.vehicles.garage import is_connected_vehicle

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

    profile = get_or_create_profile(db, user_id=current_user.user_id, display_name=display_name)
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


@router.patch("/me/preferences", response_model=UserPreferencesOut)
def patch_preferences(
    body: UserPreferencesUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserPreferencesOut:
    """Partially update the caller's preferences (api §3.1a).

    ``primary_vehicle_id`` must be one of the caller's connected vehicles, else
    ``NOT_FOUND``; ``null`` clears it.
    """

    prefs = update_preferences(
        db,
        user_id=current_user.user_id,
        changes=body,
        is_selectable_vehicle=lambda vehicle_id: is_connected_vehicle(
            db, user_id=current_user.user_id, vehicle_id=vehicle_id
        ),
    )
    db.commit()
    return UserPreferencesOut.model_validate(prefs)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={401: {"model": ErrorEnvelope, "description": "UNAUTHENTICATED."}},
)
def delete_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    smartcar: SmartcarClient = Depends(get_smartcar_client),
    supabase: SupabaseAdminClient = Depends(get_supabase_admin),
) -> Response:
    """Delete the caller's account and all of its data (api §3.1b).

    Revokes every Smartcar connection, deletes the profile (every user-owned
    table cascades) and the Supabase auth user. Steps that fail on Smartcar's
    or Supabase's side don't block the deletion; the retention job retries
    them. Idempotent: ``204`` again for an already-deleted account.
    """

    delete_account(db, smartcar, supabase, user_id=current_user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/privacy", response_model=PrivacyResponse)
def read_privacy(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrivacyResponse:
    """Consent state and retention windows for the Profile page (api §3.1c). Never writes."""

    return PrivacyResponse(
        consents=[
            _consent_status(db, current_user, kind, settings.privacy_notice_version)
            for kind in CONSENT_KINDS
        ],
        retention=RetentionOut(
            telemetry_days=settings.privacy_telemetry_retention_days,
            unsaved_plan_days=settings.privacy_unsaved_trip_days,
            refresh_log_days=settings.privacy_refresh_log_days,
        ),
    )


@router.post(
    "/me/consents",
    response_model=ConsentStatus,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": ErrorEnvelope, "description": "Not the current version."}},
)
def create_consent(
    body: ConsentCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ConsentStatus:
    """Record a consent decision (api §3.1d). Appends; history is never rewritten.

    Only the version the server currently shows can be answered, so a stale
    page can't record agreement to a notice the user didn't see.
    """

    current_version = settings.privacy_notice_version
    if body.version != current_version:
        raise ApiError(
            "VALIDATION_ERROR",
            "That is not the current version of the privacy notice. Reload and try again.",
            status_code=422,
            details={"fields": ["version"], "current_version": current_version},
        )
    record_consent(
        db,
        user_id=current_user.user_id,
        kind=body.kind,
        version=body.version,
        granted=body.granted,
    )
    db.commit()
    return _consent_status(db, current_user, body.kind, current_version)


def _consent_status(
    db: Session, user: AuthenticatedUser, kind: str, current_version: str
) -> ConsentStatus:
    record = latest_consent(db, user_id=user.user_id, kind=kind)
    if record is None:
        return ConsentStatus(kind=kind, current_version=current_version, is_current=False)  # type: ignore[arg-type]
    return ConsentStatus(
        kind=kind,  # type: ignore[arg-type]
        current_version=current_version,
        granted=record.granted,
        version=record.version,
        recorded_at=record.recorded_at,
        is_current=record.granted and record.version == current_version,
    )


__all__ = ["router"]
