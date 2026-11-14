"""HTTP routes for the vehicles module.

- ``router``: public ``/api/v1/vehicles/*`` endpoints (``docs/api-contracts.md`` §3.3).
- ``callback_router``: the internal Smartcar Connect callback. Mounted at the
  app root, outside ``/api/v1`` and excluded from the OpenAPI document. It
  never returns JSON: every path ends in a redirect to the Garage with a
  ``connect=`` flag (phase2_plan.md D3).
"""

from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.session import get_db
from app.integrations.smartcar.client import SmartcarClient, get_smartcar_client
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.users.service import get_or_create_profile
from app.modules.vehicles.redirects import DEFAULT_REDIRECT_AFTER
from app.modules.vehicles.schemas import ConnectRequest, ConnectResponse
from app.modules.vehicles.service import complete_connect
from app.modules.vehicles.state import InvalidStateError, issue_state, verify_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles", tags=["vehicles"])
callback_router = APIRouter(include_in_schema=False)

CALLBACK_PATH = "/integrations/smartcar/callback"
ConnectFlag = Literal["success", "already_connected", "denied", "error"]


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
    never returns ``CONFLICT``; the callback reports it via ``?connect=``.
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
    # The callback needs the profile row for the vehicles FK.
    get_or_create_profile(db, user_id=current_user.user_id)
    db.commit()
    return ConnectResponse(
        authorization_url=authorization_url, state=state, expires_at=claims.expires_at
    )


@callback_router.get(CALLBACK_PATH)
def smartcar_callback(
    state: str | None = Query(default=None, max_length=2048),
    user_id: str | None = Query(default=None, max_length=256),
    external_id: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=256),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    smartcar: SmartcarClient = Depends(get_smartcar_client),
) -> RedirectResponse:
    """Smartcar redirects here after Connect; we redirect on to the Garage."""

    try:
        claims = verify_state(state, secret=settings.smartcar_state_secret)
    except InvalidStateError as exc:
        logger.warning("smartcar_callback_bad_state", extra={"reason": str(exc)})
        return _to_frontend(settings, DEFAULT_REDIRECT_AFTER, "error")

    if error:
        flag: ConnectFlag = "denied" if error == "access_denied" else "error"
        logger.info("smartcar_callback_connect_error", extra={"smartcar_error": error})
        return _to_frontend(settings, claims.redirect_after, flag)

    if not user_id or (external_id is not None and external_id != str(claims.user_id)):
        logger.warning("smartcar_callback_bad_params")
        return _to_frontend(settings, claims.redirect_after, "error")

    try:
        result = complete_connect(db, smartcar, user_id=claims.user_id, sc_user_id=user_id)
        db.commit()
    except (ApiError, ValidationError) as exc:
        db.rollback()
        logger.warning(
            "smartcar_callback_upstream_failed",
            extra={"code": getattr(exc, "code", type(exc).__name__)},
        )
        return _to_frontend(settings, claims.redirect_after, "error")
    except IntegrityError:
        # A concurrent callback for the same car won the insert.
        db.rollback()
        return _to_frontend(settings, claims.redirect_after, "already_connected")

    return _to_frontend(settings, claims.redirect_after, result.outcome)


def _to_frontend(settings: Settings, path: str, flag: ConnectFlag) -> RedirectResponse:
    """302 to ``FRONTEND_BASE_URL + path`` with ``connect=<flag>`` added to its query."""

    parts = urlsplit(path)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "connect"]
    query.append(("connect", flag))
    target = urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))
    return RedirectResponse(
        url=f"{settings.frontend_base_url.rstrip('/')}{target}",
        status_code=status.HTTP_302_FOUND,
    )


__all__ = ["CALLBACK_PATH", "callback_router", "router"]
