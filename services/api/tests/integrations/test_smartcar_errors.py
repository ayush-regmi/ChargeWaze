"""``errors.from_response``: Smartcar 429s (ADR 0010) and the existing 4xx/5xx mapping."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.integrations.smartcar import errors


def _response(
    status: int, body: Any = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {}, headers=headers)


def test_app_wide_rate_limit_without_retry_after() -> None:
    exc = errors.from_response(_response(429, {"type": "RATE_LIMIT", "code": "SMARTCAR_API"}))

    assert exc.code == errors.RATE_LIMITED
    assert exc.status_code == 429
    assert exc.details == {"upstream_status": 429, "upstream_code": "SMARTCAR_API"}
    assert exc.headers is None


@pytest.mark.parametrize("header", ["soon", "-5", "0", "999999999", "1.5"])
def test_unusable_retry_after_is_dropped(header: str) -> None:
    exc = errors.from_response(
        _response(429, {"type": "RATE_LIMIT", "code": "VEHICLE"}, {"retry-after": header})
    )

    assert exc.code == errors.RATE_LIMITED
    assert "retry_after_seconds" not in (exc.details or {})
    assert exc.headers is None


def test_429_without_a_body_is_still_rate_limited() -> None:
    exc = errors.from_response(httpx.Response(429, text="slow down"))
    assert exc.code == errors.RATE_LIMITED


def test_billing_cap_stays_a_bad_gateway() -> None:
    exc = errors.from_response(
        _response(429, {"errors": [{"type": "BILLING", "code": "VEHICLE_REQUEST_LIMIT"}]})
    )
    assert exc.code == errors.UPSTREAM_ERROR
    assert exc.status_code == 502
    assert exc.details == {"upstream_status": 429, "upstream_code": "VEHICLE_REQUEST_LIMIT"}


@pytest.mark.parametrize(
    ("status", "body", "code", "upstream_code"),
    [
        (500, {"errors": [{"code": "SERVER"}]}, errors.UNREACHABLE, "SERVER"),
        (401, {"error": "invalid_client"}, errors.UPSTREAM_ERROR, "invalid_client"),
        (400, {"code": "BAD"}, errors.UPSTREAM_ERROR, "BAD"),
    ],
)
def test_other_errors_keep_their_mapping(
    status: int, body: Any, code: str, upstream_code: str
) -> None:
    exc = errors.from_response(_response(status, body))
    assert exc.code == code
    assert exc.status_code == 502
    assert exc.details == {"upstream_status": status, "upstream_code": upstream_code}
