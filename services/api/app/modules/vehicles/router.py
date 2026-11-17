"""HTTP routes for the vehicles module (``docs/api-contracts.md`` §3.2-3.6).

Connect is a two-step flow, both steps authenticated with the Supabase JWT:

1. ``POST /vehicles/connect`` signs a ``state`` for the caller and returns the
   Smartcar Connect URL.
2. Smartcar redirects the browser to the web app's
   ``/garage/connect/callback``, which forwards the query to
   ``POST /vehicles/connect/complete`` with the *current* user's token.

Step 2 requires the signed-in user to be the one inside ``state``. Without
that, an attacker could start Connect on their own account, send the
authorization URL to a victim, and receive the victim's car: ``state`` and
Smartcar's ``externalId`` would both name the attacker.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.session import get_db
from app.integrations.smartcar.client import SmartcarClient, get_smartcar_client
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.users.service import get_or_create_profile
from app.modules.vehicles.garage import (
    disconnect_vehicle,
    get_vehicle_detail,
    list_vehicles,
    update_specs,
)
from app.modules.vehicles.redirects import DEFAULT_REDIRECT_AFTER
from app.modules.vehicles.schemas import (
    ConnectCompleteRequest,
    ConnectCompleteResponse,
    ConnectFlag,
    ConnectRequest,
    ConnectResponse,
    VehicleDetail,
    VehicleList,
    VehicleSpecsOut,
    VehicleSpecsUpdate,
    VehicleState,
)
from app.modules.vehicles.service import complete_connect
from app.modules.vehicles.state import InvalidStateError, issue_state, verify_state
from app.modules.vehicles.vehicle_state import get_vehicle_state

logger = logging.getLogger(__name__)

# Smartcar Connect errors that mean the user chose not to connect, not that
# something broke: declining access, or backing out of Connect (seen on the
# real bZ as ``user_manually_returned_to_application``).
CANCELLED_CONNECT_ERRORS = frozenset({"access_denied", "user_manually_returned_to_application"})

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.post("/connect", response_model=ConnectResponse)
def start_connect(
    body: ConnectRequest | None = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    smartcar: SmartcarClient = Depends(get_smartcar_client),
) -> ConnectResponse:
    """Start Smartcar Connect and return the URL to send the browser to.

    Duplicate vehicles are only detectable after the user authorizes, so this
    never returns ``CONFLICT``; ``/connect/complete`` reports it.
    """

    request = body or ConnectRequest()
    state, claims = issue_state(
        user_id=current_user.user_id,
        redirect_after=request.redirect_after,
        secret=settings.smartcar_state_secret,
    )
    authorization_url = smartcar.build_connect_url(
        state=state, external_id=str(current_user.user_id)
    )
    # Completion needs the profile row for the vehicles FK.
    get_or_create_profile(db, user_id=current_user.user_id)
    db.commit()
    return ConnectResponse(
        authorization_url=authorization_url, state=state, expires_at=claims.expires_at
    )


@router.post("/connect/complete", response_model=ConnectCompleteResponse)
def complete_vehicle_connect(
    body: ConnectCompleteRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    smartcar: SmartcarClient = Depends(get_smartcar_client),
) -> ConnectCompleteResponse:
    """Finish Connect with the query Smartcar redirected the browser back with.

    Flow outcomes (denied, duplicate, bad state, Smartcar down) are a ``200``
    with ``result`` set, so the web app can always redirect to the Garage with
    a banner. Only a missing/invalid JWT or a malformed body is an HTTP error.
    """

    try:
        claims = verify_state(body.state, secret=settings.smartcar_state_secret)
    except InvalidStateError as exc:
        logger.warning("smartcar_connect_bad_state", extra={"reason": str(exc)})
        return _result("error")

    if claims.user_id != current_user.user_id:
        # Someone else's Connect link completed in this user's browser.
        logger.warning("smartcar_connect_session_mismatch")
        return _result("error")

    if body.error:
        logger.info("smartcar_connect_provider_error", extra={"smartcar_error": body.error})
        return _result(
            "denied" if body.error in CANCELLED_CONNECT_ERRORS else "error",
            claims.redirect_after,
        )

    if not body.user_id or (
        body.external_id is not None and body.external_id != str(claims.user_id)
    ):
        logger.warning("smartcar_connect_bad_params")
        return _result("error", claims.redirect_after)

    try:
        result = complete_connect(db, smartcar, user_id=claims.user_id, sc_user_id=body.user_id)
        db.commit()
    except (ApiError, ValidationError) as exc:
        db.rollback()
        logger.warning(
            "smartcar_connect_upstream_failed",
            extra={"code": getattr(exc, "code", type(exc).__name__)},
        )
        return _result("error", claims.redirect_after)
    except IntegrityError:
        # A concurrent completion for the same car won the insert.
        db.rollback()
        return _result("already_connected", claims.redirect_after)

    return _result(result.outcome, claims.redirect_after)


@router.get("", response_model=VehicleList)
def get_vehicles(
    include_disconnected: bool = Query(
        default=False, description="Also return vehicles the user disconnected."
    ),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VehicleList:
    """The caller's vehicles for the Garage (api §3.2). Never calls Smartcar."""

    items = list_vehicles(
        db,
        user_id=current_user.user_id,
        include_disconnected=include_disconnected,
        stale_after=_stale_after(settings),
    )
    return VehicleList(items=items)


@router.get("/{vehicle_id}", response_model=VehicleDetail)
def get_vehicle(
    vehicle_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VehicleDetail:
    """One vehicle with its specs (api §3.4). ``NOT_FOUND`` unless the caller owns it."""

    return get_vehicle_detail(
        db,
        user_id=current_user.user_id,
        vehicle_id=vehicle_id,
        stale_after=_stale_after(settings),
    )


@router.patch("/{vehicle_id}/specs", response_model=VehicleSpecsOut)
def patch_specs(
    vehicle_id: uuid.UUID,
    body: VehicleSpecsUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VehicleSpecsOut:
    """Edit capacity, connectors and charging curve (api §3.4a).

    ``NOT_FOUND`` unless the caller owns the vehicle (connected or not).
    """

    specs = update_specs(db, user_id=current_user.user_id, vehicle_id=vehicle_id, changes=body)
    db.commit()
    return specs


@router.get("/{vehicle_id}/state", response_model=VehicleState)
def get_state(
    vehicle_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VehicleState:
    """Latest cached state for the Dashboard (api §3.5).

    Reads only ``vehicles.latest_state``; never calls Smartcar. ``NOT_FOUND``
    unless the caller owns the vehicle.
    """

    return get_vehicle_state(
        db,
        user_id=current_user.user_id,
        vehicle_id=vehicle_id,
        stale_after=_stale_after(settings),
    )


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_vehicle(
    vehicle_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    smartcar: SmartcarClient = Depends(get_smartcar_client),
) -> Response:
    """Disconnect a vehicle (api §3.6). The row is kept for history.

    Returns ``204`` even when Smartcar can't be reached: the local disconnect
    is what stops telemetry being accepted, and revocation can be retried.
    """

    disconnect_vehicle(db, smartcar, user_id=current_user.user_id, vehicle_id=vehicle_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _stale_after(settings: Settings) -> timedelta:
    return timedelta(minutes=settings.telemetry_stale_after_minutes)


def _result(
    result: ConnectFlag, redirect_after: str = DEFAULT_REDIRECT_AFTER
) -> ConnectCompleteResponse:
    return ConnectCompleteResponse(result=result, redirect_after=redirect_after)


__all__ = ["router"]
