"""Map Mapbox transport and HTTP failures onto the ChargeWaze error envelope.

Callers never see ``httpx`` exceptions: the client converts them here. Mapbox
error *messages* are never passed on; they can echo the request.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import status

from app.core.errors import ApiError

UNREACHABLE = "MAPBOX_UNREACHABLE"
UPSTREAM_ERROR = "MAPBOX_ERROR"
NOT_CONFIGURED = "MAPBOX_NOT_CONFIGURED"


def not_configured() -> ApiError:
    return ApiError(
        NOT_CONFIGURED,
        "Mapbox is not configured on the server.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def unexpected_response(upstream_code: str | None = None) -> ApiError:
    details: dict[str, Any] | None = {"upstream_code": upstream_code} if upstream_code else None
    return ApiError(
        UPSTREAM_ERROR,
        "Mapbox returned an unexpected response.",
        status_code=status.HTTP_502_BAD_GATEWAY,
        details=details,
    )


def from_transport_error(exc: httpx.TransportError) -> ApiError:
    """Timeouts become 504; every other network failure becomes 502."""

    if isinstance(exc, httpx.TimeoutException):
        return ApiError(
            UNREACHABLE,
            "Mapbox did not respond in time.",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    return ApiError(
        UNREACHABLE,
        "Could not reach Mapbox.",
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


def from_response(response: httpx.Response) -> ApiError:
    """Turn a non-2xx Mapbox response into an ``ApiError``.

    - 401/403: the server token is missing, invalid or lacks a scope → 503
      ``MAPBOX_NOT_CONFIGURED`` (it's our configuration, not the caller's fault).
    - 5xx: Mapbox is down → 502 ``MAPBOX_UNREACHABLE``.
    - Any other 4xx: Mapbox refused the request → 502 ``MAPBOX_ERROR``.
    """

    if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
        return not_configured()

    details: dict[str, Any] = {"upstream_status": response.status_code}
    upstream_code = _upstream_error_code(response)
    if upstream_code:
        details["upstream_code"] = upstream_code

    if response.status_code >= 500:
        return ApiError(
            UNREACHABLE,
            "Mapbox is unavailable.",
            status_code=status.HTTP_502_BAD_GATEWAY,
            details=details,
        )
    return ApiError(
        UPSTREAM_ERROR,
        "Mapbox rejected the request.",
        status_code=status.HTTP_502_BAD_GATEWAY,
        details=details,
    )


def _upstream_error_code(response: httpx.Response) -> str | None:
    """Best-effort read of Directions' top-level ``code`` (e.g. ``InvalidInput``).

    Search Box errors put an opaque, base64-ish string in ``message.code``; it's
    not useful to callers, so it's ignored.
    """

    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict) and isinstance(body.get("code"), str):
        return str(body["code"])
    return None


__all__ = [
    "NOT_CONFIGURED",
    "UNREACHABLE",
    "UPSTREAM_ERROR",
    "from_response",
    "from_transport_error",
    "not_configured",
    "unexpected_response",
]
