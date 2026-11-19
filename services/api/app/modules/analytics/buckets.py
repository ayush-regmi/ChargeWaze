"""Date ranges and time buckets for analytics (phase5_plan.md D13).

Pure functions. ``from`` and ``to`` are local dates in an IANA zone,
inclusive. Buckets are local days, ISO weeks (Monday start) or calendar
months, and a leg belongs to the bucket of its ``end_time`` in that zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import ApiError

GroupBy = Literal["day", "week", "month"]

DEFAULT_RANGE_DAYS = 30


@dataclass(frozen=True)
class Bucket:
    label: str  # 2026-11-08, 2026-W45 or 2026-11
    first_day: date  # local date the bucket starts on
    start: datetime  # first_day's local midnight, in UTC


@dataclass(frozen=True)
class DateRange:
    first: date  # inclusive, local
    last: date  # inclusive, local
    start: datetime  # first's local midnight, in UTC
    end: datetime  # the local midnight after last, in UTC (exclusive)


def resolve_zone(name: str) -> ZoneInfo:
    """The IANA zone ``name``; anything else is a 422 ``VALIDATION_ERROR``."""

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise _validation("Unknown time zone.", reason="UNKNOWN_TIME_ZONE") from None


def resolve_range(
    first: date | None, last: date | None, *, zone: ZoneInfo, now: datetime
) -> DateRange:
    """Fill defaults (the last 30 days up to today in ``zone``) and check the order."""

    if last is None:
        last = (
            now.astimezone(zone).date()
            if first is None
            else max(first, now.astimezone(zone).date())
        )
    if first is None:
        first = last - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    if first > last:
        raise _validation("'from' must not be after 'to'.", reason="INVALID_RANGE")
    try:
        return DateRange(
            first=first,
            last=last,
            start=local_midnight_utc(first, zone),
            end=local_midnight_utc(last + timedelta(days=1), zone),
        )
    except OverflowError:
        raise _validation("Date out of range.", reason="INVALID_RANGE") from None


def bucket_first_day(day: date, group_by: GroupBy) -> date:
    if group_by == "day":
        return day
    if group_by == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def bucket_label(first_day: date, group_by: GroupBy) -> str:
    if group_by == "day":
        return first_day.isoformat()
    if group_by == "week":
        iso = first_day.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return f"{first_day.year:04d}-{first_day.month:02d}"


def bucket_count(rng: DateRange, group_by: GroupBy) -> int:
    first = bucket_first_day(rng.first, group_by)
    last = bucket_first_day(rng.last, group_by)
    if group_by == "day":
        return (last - first).days + 1
    if group_by == "week":
        return (last - first).days // 7 + 1
    return (last.year - first.year) * 12 + (last.month - first.month) + 1


def build_buckets(
    rng: DateRange, group_by: GroupBy, *, zone: ZoneInfo, max_buckets: int
) -> list[Bucket]:
    """Every bucket that overlaps ``rng``, oldest first. More than ``max_buckets`` is a 422."""

    count = bucket_count(rng, group_by)
    if count > max_buckets:
        raise _validation(
            f"The range has {count} buckets; the limit is {max_buckets}.",
            reason="RANGE_TOO_LARGE",
            extra={"buckets": count, "max_buckets": max_buckets},
        )
    buckets: list[Bucket] = []
    day = bucket_first_day(rng.first, group_by)
    for _ in range(count):
        buckets.append(
            Bucket(
                label=bucket_label(day, group_by),
                first_day=day,
                start=local_midnight_utc(day, zone),
            )
        )
        day = _next_first_day(day, group_by)
    return buckets


def bucket_of(moment: datetime, group_by: GroupBy, *, zone: ZoneInfo) -> date:
    """The first local day of the bucket that ``moment`` falls in."""

    return bucket_first_day(moment.astimezone(zone).date(), group_by)


def local_midnight_utc(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC)


def _next_first_day(day: date, group_by: GroupBy) -> date:
    if group_by == "day":
        return day + timedelta(days=1)
    if group_by == "week":
        return day + timedelta(days=7)
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _validation(message: str, *, reason: str, extra: dict[str, int] | None = None) -> ApiError:
    return ApiError(
        "VALIDATION_ERROR",
        message,
        status_code=422,
        details={"reason": reason, **(extra or {})},
    )
