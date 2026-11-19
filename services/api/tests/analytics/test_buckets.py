"""Buckets and ranges (phase5_plan.md D13)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import ApiError
from app.modules.analytics import buckets

UTC_ZONE = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")


def _range(first: date, last: date, zone: ZoneInfo = UTC_ZONE) -> buckets.DateRange:
    return buckets.resolve_range(first, last, zone=zone, now=datetime(2026, 11, 19, tzinfo=UTC))


def test_day_buckets_cover_every_day_including_empty_ones() -> None:
    rng = _range(date(2026, 11, 2), date(2026, 11, 8))
    result = buckets.build_buckets(rng, "day", zone=UTC_ZONE, max_buckets=366)
    assert [b.label for b in result] == [f"2026-11-0{d}" for d in range(2, 9)]
    assert result[0].start == datetime(2026, 11, 2, tzinfo=UTC)


def test_bucket_start_is_local_midnight_written_in_utc() -> None:
    # 2026-11-01 is the US DST change: midnight is still EDT (-4), the next is EST (-5).
    rng = _range(date(2026, 11, 1), date(2026, 11, 2), NEW_YORK)
    result = buckets.build_buckets(rng, "day", zone=NEW_YORK, max_buckets=366)
    assert [b.start for b in result] == [
        datetime(2026, 11, 1, 4, tzinfo=UTC),
        datetime(2026, 11, 2, 5, tzinfo=UTC),
    ]
    assert rng.end == datetime(2026, 11, 3, 5, tzinfo=UTC)


def test_leg_is_bucketed_by_local_end_date() -> None:
    end = datetime(2026, 11, 2, 3, 30, tzinfo=UTC)
    assert buckets.bucket_of(end, "day", zone=NEW_YORK) == date(2026, 11, 1)
    assert buckets.bucket_of(end, "day", zone=UTC_ZONE) == date(2026, 11, 2)


def test_iso_week_labels_across_a_year_boundary() -> None:
    rng = _range(date(2026, 12, 30), date(2027, 1, 12))
    result = buckets.build_buckets(rng, "week", zone=UTC_ZONE, max_buckets=366)
    assert [b.label for b in result] == ["2026-W53", "2027-W01", "2027-W02"]
    # Weeks start on Monday, even before ``from``.
    assert result[0].first_day == date(2026, 12, 28)
    assert buckets.bucket_of(datetime(2027, 1, 1, 12, tzinfo=UTC), "week", zone=UTC_ZONE) == (
        date(2026, 12, 28)
    )


def test_month_buckets_roll_over_december() -> None:
    rng = _range(date(2026, 11, 15), date(2027, 2, 1))
    result = buckets.build_buckets(rng, "month", zone=UTC_ZONE, max_buckets=366)
    assert [b.label for b in result] == ["2026-11", "2026-12", "2027-01", "2027-02"]


def test_bucket_cap() -> None:
    ok = _range(date(2025, 1, 1), date(2026, 1, 1))  # 366 days
    assert len(buckets.build_buckets(ok, "day", zone=UTC_ZONE, max_buckets=366)) == 366
    too_many = _range(date(2025, 1, 1), date(2026, 1, 2))
    with pytest.raises(ApiError) as exc:
        buckets.build_buckets(too_many, "day", zone=UTC_ZONE, max_buckets=366)
    assert exc.value.status_code == 422
    assert exc.value.details == {"reason": "RANGE_TOO_LARGE", "buckets": 367, "max_buckets": 366}
    # The same range by week is fine.
    assert len(buckets.build_buckets(too_many, "week", zone=UTC_ZONE, max_buckets=366)) == 53


def test_default_range_is_the_last_30_days_in_the_zone() -> None:
    now = datetime(2026, 11, 19, 2, 0, tzinfo=UTC)  # still 2026-11-18 in New York
    rng = buckets.resolve_range(None, None, zone=NEW_YORK, now=now)
    assert (rng.first, rng.last) == (date(2026, 10, 20), date(2026, 11, 18))
    only_to = buckets.resolve_range(None, date(2026, 1, 30), zone=UTC_ZONE, now=now)
    assert only_to.first == date(2026, 1, 1)


def test_from_after_to_is_a_validation_error() -> None:
    with pytest.raises(ApiError) as exc:
        _range(date(2026, 11, 9), date(2026, 11, 8))
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.details == {"reason": "INVALID_RANGE"}


def test_far_future_date_is_a_validation_error() -> None:
    with pytest.raises(ApiError):
        _range(date(9999, 12, 31), date(9999, 12, 31))


@pytest.mark.parametrize("name", ["Mars/Olympus", "", "../etc/passwd", "America"])
def test_unknown_time_zone(name: str) -> None:
    with pytest.raises(ApiError) as exc:
        buckets.resolve_zone(name)
    assert exc.value.details == {"reason": "UNKNOWN_TIME_ZONE"}
