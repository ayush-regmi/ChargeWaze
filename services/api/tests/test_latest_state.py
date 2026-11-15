"""Unit tests for reading ``vehicles.latest_state`` (F4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.modules.vehicles.latest_state import is_stale, last_recorded_at

T0 = datetime(2026, 11, 6, 18, 52, tzinfo=UTC)


@pytest.mark.parametrize(
    "state",
    [None, {}, {"signals": {}}, {"recorded_at": None}, {"recorded_at": 123}, "x", []],
)
def test_no_timestamp(state: Any) -> None:
    assert last_recorded_at(state) is None


def test_top_level_timestamp() -> None:
    assert last_recorded_at({"recorded_at": "2026-11-06T18:52:00Z"}) == T0


def test_max_of_top_level_and_signals() -> None:
    state = {
        "recorded_at": "2026-11-06T18:52:00Z",
        "signals": {
            "soc": {"recorded_at": "2026-11-06T18:00:00Z"},
            "range": {"recorded_at": "2026-11-06T19:00:00+00:00"},
            "bad": {"recorded_at": "nope"},
            "odd": "not-a-dict",
        },
    }
    assert last_recorded_at(state) == T0 + timedelta(minutes=8)


def test_naive_timestamp_treated_as_utc() -> None:
    assert last_recorded_at({"recorded_at": "2026-11-06T18:52:00"}) == T0


def test_is_stale() -> None:
    window = timedelta(minutes=15)
    assert is_stale(None, stale_after=window, now=T0) is True
    assert is_stale(T0, stale_after=window, now=T0 + timedelta(minutes=15)) is False
    assert is_stale(T0, stale_after=window, now=T0 + timedelta(minutes=16)) is True
