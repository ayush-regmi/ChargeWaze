"""Signed, short-lived Smartcar Connect ``state`` (phase2_plan.md D2).

Stateless: an HS256 JWT keyed with ``SMARTCAR_STATE_SECRET`` that carries the
ChargeWaze user ID, the validated ``redirect_after`` path and a random nonce.
There is no code exchange under ``response_type=none``, so ``state`` can't be
made single-use. Completion requires the signed-in user to equal ``sub``, so a
replayed ``state`` only re-runs completion for its own user, which is
idempotent (duplicates are detected).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import status

from app.core.errors import ApiError
from app.modules.vehicles.redirects import is_safe_redirect_path

STATE_TTL = timedelta(minutes=10)
STATE_ISSUER = "chargewaze-api"
STATE_AUDIENCE = "smartcar-connect"
STATE_ALGORITHM = "HS256"
# RFC 7518 §3.2: an HS256 key must be at least 256 bits.
MIN_SECRET_BYTES = 32


class InvalidStateError(Exception):
    """The ``state`` is missing, tampered with, expired or malformed."""


@dataclass(frozen=True)
class ConnectState:
    user_id: uuid.UUID
    redirect_after: str
    expires_at: datetime


def _require_secret(secret: str) -> bytes:
    key = secret.encode("utf-8")
    if len(key) < MIN_SECRET_BYTES:
        raise ApiError(
            "SMARTCAR_NOT_CONFIGURED",
            "Smartcar Connect state secret is not configured.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return key


def issue_state(
    *,
    user_id: uuid.UUID,
    redirect_after: str,
    secret: str,
    now: datetime | None = None,
) -> tuple[str, ConnectState]:
    """Sign a new ``state``. ``redirect_after`` must already be validated."""

    if not is_safe_redirect_path(redirect_after):
        raise ValueError("redirect_after must be a safe same-origin path.")
    key = _require_secret(secret)
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued_at + STATE_TTL
    token = jwt.encode(
        {
            "iss": STATE_ISSUER,
            "aud": STATE_AUDIENCE,
            "sub": str(user_id),
            "ra": redirect_after,
            "nonce": secrets.token_urlsafe(16),
            "iat": issued_at,
            "exp": expires_at,
        },
        key,
        algorithm=STATE_ALGORITHM,
    )
    claims = ConnectState(user_id=user_id, redirect_after=redirect_after, expires_at=expires_at)
    return token, claims


def verify_state(token: str | None, *, secret: str) -> ConnectState:
    """Decode and validate a ``state``. Raises ``InvalidStateError`` on any problem."""

    if not token:
        raise InvalidStateError("missing")
    try:
        key = _require_secret(secret)
    except ApiError as exc:
        raise InvalidStateError("not configured") from exc
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[STATE_ALGORITHM],
            audience=STATE_AUDIENCE,
            issuer=STATE_ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidStateError(type(exc).__name__) from None

    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise InvalidStateError("bad sub") from exc
    redirect_after = claims.get("ra")
    if not isinstance(redirect_after, str) or not is_safe_redirect_path(redirect_after):
        raise InvalidStateError("bad redirect")
    return ConnectState(
        user_id=user_id,
        redirect_after=redirect_after,
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
    )


__all__ = [
    "STATE_TTL",
    "ConnectState",
    "InvalidStateError",
    "issue_state",
    "verify_state",
]
