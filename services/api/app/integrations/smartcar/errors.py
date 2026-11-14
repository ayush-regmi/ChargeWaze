"""Map Smartcar transport and HTTP failures onto the Chargewise error envelope.

Callers never see ``httpx`` exceptions: the client converts them here.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import status

from app.core.errors import ApiError

UNREACHABLE = "SMARTCAR_UNREACHABLE"
UPSTREAM_ERROR = "SMARTCAR_ERROR"


def from_transport_error(exc: httpx.TransportError) -> ApiError:
    """Timeouts become 504; every other network failure becomes 502."""

    if isinstance(exc, httpx.TimeoutException):
        return ApiError(
            UNREACHABLE,
            "Smartcar did not respond in time.",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    return ApiError(
        UNREACHABLE,
        "Could not reach Smartcar.",
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


def from_response(response: httpx.Response) -> ApiError:
    """Turn a non-2xx Smartcar response into an ``ApiError``.

    5xx means Smartcar is down (``SMARTCAR_UNREACHABLE``); 4xx means Smartcar
    refused the request (``SMARTCAR_ERROR``). Either way it is a bad gateway from
    our client's point of view. Only Smartcar's error *code* is passed on —
    their messages can echo request details.
    """

    details: dict[str, Any] = {"upstream_status": response.status_code}
    upstream_code = _upstream_error_code(response)
    if upstream_code:
        details["upstream_code"] = upstream_code

    if response.status_code >= 500:
        return ApiError(
            UNREACHABLE,
            "Smartcar is unavailable.",
            status_code=status.HTTP_502_BAD_GATEWAY,
            details=details,
        )
    return ApiError(
        UPSTREAM_ERROR,
        "Smartcar rejected the request.",
        status_code=status.HTTP_502_BAD_GATEWAY,
        details=details,
    )


def _upstream_error_code(response: httpx.Response) -> str | None:
    """Best-effort read of ``errors[0].code`` (V3) or ``code`` (OAuth/V2 shapes)."""

    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        code = errors[0].get("code")
        return str(code) if code is not None else None
    for key in ("code", "error"):
        if isinstance(body.get(key), str):
            return str(body[key])
    return None


__all__ = ["UNREACHABLE", "UPSTREAM_ERROR", "from_response", "from_transport_error"]
