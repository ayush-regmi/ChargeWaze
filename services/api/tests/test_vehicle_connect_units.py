"""Unit tests for Connect helpers: redirect validation, signed state, spec catalog."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.errors import ApiError
from app.modules.vehicles.redirects import is_safe_redirect_path, safe_redirect_path
from app.modules.vehicles.spec_catalog import CatalogSpec, lookup_spec
from app.modules.vehicles.state import InvalidStateError, issue_state, verify_state

SECRET = "unit-test-state-secret-0123456789abcdef"
USER = uuid.UUID("33333333-3333-3333-3333-333333333333")


@pytest.mark.parametrize(
    "path", ["/garage", "/garage?connect=x", "/garage#cars", "/a/b-c_d.e", "/%7Euser"]
)
def test_safe_paths(path: str) -> None:
    assert is_safe_redirect_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "garage",
        "//evil.example",
        "/\\evil.example",
        "/garage\\..\\x",
        "https://evil.example",
        "/.//evil",
        "/a/../b",
        "/a/%2e%2e/b",
        "/a/./b",
        "/a//b",
        "/sp ace",
        "/tab\t",
        "/nl\n",
        "/" + "a" * 600,
    ],
)
def test_unsafe_paths(path: str) -> None:
    assert not is_safe_redirect_path(path)
    assert safe_redirect_path(path) == "/garage"


def test_state_round_trip() -> None:
    token, issued = issue_state(user_id=USER, redirect_after="/garage", secret=SECRET)
    verified = verify_state(token, secret=SECRET)
    assert verified.user_id == USER
    assert verified.redirect_after == "/garage"
    assert verified.expires_at == issued.expires_at


def test_state_nonce_differs_each_time() -> None:
    a, _ = issue_state(user_id=USER, redirect_after="/garage", secret=SECRET)
    b, _ = issue_state(user_id=USER, redirect_after="/garage", secret=SECRET)
    assert a != b


def test_state_wrong_secret() -> None:
    token, _ = issue_state(user_id=USER, redirect_after="/garage", secret=SECRET)
    with pytest.raises(InvalidStateError):
        verify_state(token, secret=SECRET + "x")


def test_state_expired() -> None:
    token, _ = issue_state(
        user_id=USER,
        redirect_after="/garage",
        secret=SECRET,
        now=datetime.now(UTC) - timedelta(minutes=10, seconds=5),
    )
    with pytest.raises(InvalidStateError):
        verify_state(token, secret=SECRET)


def test_state_rejects_short_secret() -> None:
    with pytest.raises(ApiError) as exc:
        issue_state(user_id=USER, redirect_after="/garage", secret="short")
    assert exc.value.code == "SMARTCAR_NOT_CONFIGURED"
    with pytest.raises(InvalidStateError):
        verify_state("anything", secret="")


def test_state_refuses_unsafe_redirect() -> None:
    with pytest.raises(ValueError):
        issue_state(user_id=USER, redirect_after="//evil", secret=SECRET)


def test_catalog_lookup() -> None:
    catalog = (
        CatalogSpec("Toyota", "bZ", 2025, 2026, Decimal("1"), ("NACS",), source="test"),
        CatalogSpec("Toyota", "bZ XLE", 2026, 2026, Decimal("2"), ("NACS",), source="test"),
    )
    hit = lookup_spec("TOYOTA", "bZ XLE AWD", 2026, catalog=catalog)
    assert hit is not None and hit.usable_capacity_kwh == Decimal("2")
    assert lookup_spec("TOYOTA", "bZ Limited", 2025, catalog=catalog) is not None
    assert lookup_spec("TOYOTA", "bZ XLE AWD", 2024, catalog=catalog) is None
    assert lookup_spec("Honda", "bZ", 2026, catalog=catalog) is None


def test_default_catalog_has_sources() -> None:
    from app.modules.vehicles.spec_catalog import CATALOG

    assert all(entry.source.strip() for entry in CATALOG)
