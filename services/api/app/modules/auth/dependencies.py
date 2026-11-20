"""FastAPI dependencies for authenticating Supabase-signed JWTs.

The frontend obtains access tokens from Supabase Auth and sends them as
``Authorization: Bearer <token>``. This module verifies each token against
the project's JWKS endpoint (or, as a fallback, a shared HS256 secret) and
exposes the resolved user to route handlers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.session import get_db
from app.modules.auth.access import enforce_access

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    """Minimal view of the signed-in Supabase user used across modules."""

    user_id: uuid.UUID
    email: str | None
    raw_claims: dict[str, object]


class JwtVerifier:
    """Verify a Supabase-signed JWT using JWKS (preferred) or HS256 fallback."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks_client: PyJWKClient | None = None
        if settings.supabase_jwks_url:
            self._jwks_client = PyJWKClient(settings.supabase_jwks_url)

    def verify(self, token: str) -> dict[str, object]:
        audience = self._settings.supabase_jwt_audience or "authenticated"

        if self._jwks_client is not None:
            try:
                signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            except jwt.PyJWKClientError as exc:
                raise ApiError(
                    code="UNAUTHENTICATED",
                    message="Unable to resolve signing key for token.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                ) from exc
            return self._decode(
                token,
                key=signing_key.key,
                algorithms=[signing_key.algorithm_name] if signing_key.algorithm_name else None,
                audience=audience,
            )

        if not self._settings.supabase_jwt_secret:
            raise ApiError(
                code="INTERNAL_ERROR",
                message="Auth is not configured: set SUPABASE_JWKS_URL or SUPABASE_JWT_SECRET.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return self._decode(
            token,
            key=self._settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=audience,
        )

    @staticmethod
    def _decode(
        token: str,
        *,
        key: object,
        algorithms: list[str] | None,
        audience: str,
    ) -> dict[str, object]:
        try:
            return jwt.decode(
                token,
                key=key,  # type: ignore[arg-type]
                algorithms=algorithms,
                audience=audience,
                options={"require": ["exp", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ApiError(
                code="UNAUTHENTICATED",
                message="Access token has expired.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise ApiError(
                code="UNAUTHENTICATED",
                message="Access token has the wrong audience.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise ApiError(
                code="UNAUTHENTICATED",
                message="Access token is invalid.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            ) from exc


@lru_cache(maxsize=1)
def _default_verifier() -> JwtVerifier:
    return JwtVerifier(get_settings())


def _get_verifier(request: Request) -> JwtVerifier:
    """Allow tests to override the verifier via ``app.state.jwt_verifier``."""

    override = getattr(request.app.state, "jwt_verifier", None)
    if isinstance(override, JwtVerifier):
        return override
    return _default_verifier()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    """The verified caller, after the demo/live gate (``access.enforce_access``).

    ``db`` is the request's shared session (FastAPI caches dependencies per
    request); it opens a connection only in demo mode.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(
            code="UNAUTHENTICATED",
            message="Missing bearer token.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    verifier = _get_verifier(request)
    claims = verifier.verify(credentials.credentials)

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise ApiError(
            code="UNAUTHENTICATED",
            message="Token missing subject claim.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise ApiError(
            code="UNAUTHENTICATED",
            message="Token subject is not a UUID.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    enforce_access(request, user_id=user_id, settings=settings, db=db)

    email_claim = claims.get("email")
    email = email_claim if isinstance(email_claim, str) else None

    return AuthenticatedUser(user_id=user_id, email=email, raw_claims=claims)


__all__ = ["AuthenticatedUser", "JwtVerifier", "bearer_scheme", "get_current_user"]
