"""Map station-provider transport and HTTP failures onto the ChargeWaze error envelope.

Callers never see ``httpx`` exceptions. Provider error *bodies* are never passed
on: AFDC's can echo the request, which may include the API key.
"""

from __future__ import annotations

import httpx
from fastapi import status

from app.core.errors import ApiError

UNREACHABLE = "STATIONS_UNREACHABLE"
NOT_CONFIGURED = "STATIONS_NOT_CONFIGURED"


def not_configured() -> ApiError:
    return ApiError(
        NOT_CONFIGURED,
        "The charging station provider is not configured on the server.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def unexpected_response() -> ApiError:
    return ApiError(
        UNREACHABLE,
        "The charging station provider returned an unexpected response.",
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


def from_transport_error(exc: httpx.TransportError) -> ApiError:
    """Timeouts become 504; every other network failure becomes 502."""

    if isinstance(exc, httpx.TimeoutException):
        return ApiError(
            UNREACHABLE,
            "The charging station provider did not respond in time.",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    return ApiError(
        UNREACHABLE,
        "Could not reach the charging station provider.",
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


def from_response(response: httpx.Response) -> ApiError:
    """Turn a non-2xx provider response into an ``ApiError``.

    - 401/403: our API key is missing or rejected → 503 ``STATIONS_NOT_CONFIGURED``.
    - Anything else (4xx incl. 422/429, 5xx) → 502 ``STATIONS_UNREACHABLE`` with
      the upstream status in ``details``.
    """

    if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
        return not_configured()
    return ApiError(
        UNREACHABLE,
        "The charging station provider is unavailable.",
        status_code=status.HTTP_502_BAD_GATEWAY,
        details={"upstream_status": response.status_code},
    )


__all__ = [
    "NOT_CONFIGURED",
    "UNREACHABLE",
    "from_response",
    "from_transport_error",
    "not_configured",
    "unexpected_response",
]
