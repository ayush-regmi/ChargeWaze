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
RATE_LIMITED = "SMARTCAR_RATE_LIMITED"
# Longest ``retry-after`` we pass on; anything larger is treated as missing.
MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60


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
    our client's point of view. The exception is a ``429`` of type
    ``RATE_LIMIT`` (per-vehicle or app-wide throttle), which stays a ``429``
    (``SMARTCAR_RATE_LIMITED``) so the caller can wait and retry; the monthly
    ``BILLING`` cap is not something waiting fixes, so it stays a ``502``.
    Only Smartcar's error *type* and *code* are passed on — their messages can
    echo request details.
    """

    details: dict[str, Any] = {"upstream_status": response.status_code}
    upstream_type, upstream_code = _upstream_error(response)
    if upstream_code:
        details["upstream_code"] = upstream_code

    if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS and upstream_type != "BILLING":
        retry_after = _retry_after_seconds(response)
        headers: dict[str, str] | None = None
        if retry_after is not None:
            details["retry_after_seconds"] = retry_after
            headers = {"Retry-After": str(retry_after)}
        return ApiError(
            RATE_LIMITED,
            "Smartcar is limiting requests for this vehicle; try again later.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details=details,
            headers=headers,
        )

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


def _upstream_error(response: httpx.Response) -> tuple[str | None, str | None]:
    """Best-effort ``(type, code)``.

    Reads ``errors[0]`` (V3 list shape), else top-level ``type``/``code``
    (V3 single-error and OAuth/V2 shapes).
    """

    try:
        body = response.json()
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        return _str_or_none(first.get("type")), _str_or_none(first.get("code"))
    code = next((str(body[k]) for k in ("code", "error") if isinstance(body.get(k), str)), None)
    return _str_or_none(body.get("type")), code


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def _retry_after_seconds(response: httpx.Response) -> int | None:
    """``retry-after`` in whole seconds (Smartcar sends seconds), or ``None``."""

    raw = response.headers.get("retry-after", "").strip()
    if not raw.isdigit():
        return None
    seconds = int(raw)
    return seconds if 0 < seconds <= MAX_RETRY_AFTER_SECONDS else None


__all__ = [
    "RATE_LIMITED",
    "UNREACHABLE",
    "UPSTREAM_ERROR",
    "from_response",
    "from_transport_error",
]
