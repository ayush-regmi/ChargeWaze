"""Trip inference: telemetry readings → driving legs (Phase 5 F2).

Pure functions, no database and no HTTP. ``observed_service`` (P5-F3) reads the
readings, resolves capacity and stores what :func:`infer_legs` returns.

The rules come from ``docs/phase5_plan.md``: §2 D1 (one leg is one stretch of
odometer increase with no charging inside it), D8 (quality), D10 (capacity),
D11 (simulated data), D14 (open legs) and the algorithm in §4.1. The worked
examples in §5 are the test fixtures in ``fixtures/telemetry/``.

Choices the plan leaves open (approved with the F2 plan; see the F2 "As built"
notes in the phase plan):

* Counters count :class:`Reading` objects, i.e. one per signal, like the
  ``vehicle_telemetry`` rows they come from.
* ``closed`` depends only on the readings (§5 example 2). Whether a leg is
  still *in progress* (D14) also depends on the clock: :func:`is_in_progress`.
* ``odometer_regression`` caps a leg at ``medium``.
* A moving odometer pair with a charging session inside it becomes a leg of
  its own (bounded start, no energy); the driving after it starts a new leg.
* A charging session with no "not charging" reading ends at the first moving
  odometer pair after it started (at that pair's later reading, an upper
  bound), so one lost event can't spoil every later leg.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal, get_args

SignalKind = Literal["odometer_km", "soc_percent", "is_charging"]
QualityFlag = Literal["high", "medium", "low"]
QualityReason = Literal[
    "soc_missing",
    "soc_increased",
    "capacity_unknown",
    "soc_delta_small",
    "soc_reading_stale",
    "start_time_bounded",
    "odometer_regression",
]

_REASON_ORDER: tuple[QualityReason, ...] = get_args(QualityReason)


@dataclass(frozen=True)
class Reading:
    recorded_at: datetime  # provider timestamp (vehicle_telemetry.recorded_at)
    signal: SignalKind
    value: float  # km, percent, or 1.0/0.0 for is_charging
    is_simulated: bool


@dataclass(frozen=True)
class InferenceSettings:
    stop_split_minutes: float = 20
    min_move_km: float = 0.1
    min_leg_km: float = 0.5
    high_min_soc_delta: float = 10
    medium_min_soc_delta: float = 4
    high_max_soc_age_minutes: float = 15
    medium_max_soc_age_minutes: float = 360
    soc_resolution_points: float = 1
    soc_increase_tolerance_points: float = 1


@dataclass(frozen=True)
class InferredLeg:
    start_time: datetime
    start_time_latest: datetime
    end_time: datetime
    odometer_start_km: float
    odometer_end_km: float
    distance_km: float
    soc_start_percent: float | None
    soc_start_recorded_at: datetime | None
    soc_end_percent: float | None
    soc_end_recorded_at: datetime | None
    energy_kwh: float | None
    energy_kwh_uncertainty: float | None
    quality_flag: QualityFlag
    quality_reasons: tuple[QualityReason, ...]  # in the order of the Literal above
    closed: bool


@dataclass(frozen=True)
class ChargingSession:  # returned for tests and F4; not stored in v1
    start: datetime
    end: datetime | None  # None while still charging
    soc_start_percent: float | None
    soc_end_percent: float | None


@dataclass(frozen=True)
class InferenceResult:
    legs: tuple[InferredLeg, ...]  # by start_time
    charging_sessions: tuple[ChargingSession, ...]
    dropped_simulated: int
    dropped_duplicates: int
    dropped_odometer_regressions: int
    dropped_short_legs: int


# --------------------------------------------------------------------------- internals


@dataclass(frozen=True)
class _Point:
    t: datetime
    v: float


@dataclass(frozen=True)
class _Pair:
    p: _Point
    q: _Point
    moving: bool


@dataclass
class _OpenLeg:
    start_time: datetime
    start_time_latest: datetime
    end_time: datetime
    odometer_start_km: float
    odometer_end_km: float
    # A leg made of one moving pair with charging inside it: nothing may extend it.
    sealed: bool = False
    closed: bool = False


@dataclass
class _Interval:
    start: datetime
    end: datetime | None = None


def _usable(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _clean(
    readings: Iterable[Reading], vehicle_is_simulated: bool
) -> tuple[list[Reading], int, int]:
    """Step 1: drop simulated mismatches and unusable values, sort, collapse duplicates."""

    kept: list[Reading] = []
    dropped_simulated = 0
    for r in readings:
        if r.is_simulated != vehicle_is_simulated:
            dropped_simulated += 1
            continue
        if not _usable(r.value):
            continue  # errored rows (value NULL) carry no measurement
        kept.append(r)
    # value in the key makes the order total, so any input order gives one result.
    kept.sort(key=lambda r: (r.recorded_at, r.signal, r.value))
    unique: list[Reading] = []
    dropped_duplicates = 0
    for r in kept:
        if unique and (unique[-1].recorded_at, unique[-1].signal, unique[-1].value) == (
            r.recorded_at,
            r.signal,
            r.value,
        ):
            dropped_duplicates += 1
            continue
        unique.append(r)
    return unique, dropped_simulated, dropped_duplicates


def _odometer_points(
    readings: Sequence[Reading], min_move_km: float
) -> tuple[list[_Point], list[datetime]]:
    """Odometer readings with regressions (below the running max by > min_move) removed."""

    points: list[_Point] = []
    regressions: list[datetime] = []
    running_max = -math.inf
    for r in readings:
        if r.signal != "odometer_km":
            continue
        if r.value < running_max - min_move_km:
            regressions.append(r.recorded_at)
            continue
        points.append(_Point(r.recorded_at, float(r.value)))
        running_max = max(running_max, r.value)
    return points, regressions


def _charging_intervals(readings: Sequence[Reading], pairs: Sequence[_Pair]) -> list[_Interval]:
    """Step 2: sessions from ``is_charging`` transitions.

    A session with no closing 0 ends at the first moving odometer pair that starts
    at or after it (end = that pair's later reading, an upper bound).
    """

    moving = [pr for pr in pairs if pr.moving]

    def motion_cut(start: datetime, before: datetime | None) -> datetime | None:
        for pr in moving:
            if pr.p.t >= start and (before is None or pr.q.t <= before):
                return pr.q.t
            if before is not None and pr.p.t > before:
                break
        return None

    sessions: list[_Interval] = []
    current: _Interval | None = None
    for r in readings:
        if r.signal != "is_charging":
            continue
        if current is not None:
            cut = motion_cut(current.start, r.recorded_at)
            if cut is not None:
                current.end = cut
                sessions.append(current)
                current = None
        charging = r.value >= 0.5
        if charging and current is None:
            current = _Interval(start=r.recorded_at)
        elif not charging and current is not None:
            current.end = r.recorded_at
            sessions.append(current)
            current = None
    if current is not None:
        current.end = motion_cut(current.start, None)
        sessions.append(current)
    return sessions


def _overlaps(sessions: Sequence[_Interval], a: datetime, b: datetime) -> bool:
    """A session overlaps the open interval (a, b)."""

    return any(s.start < b and (s.end is None or s.end > a) for s in sessions)


def _build_legs(
    pairs: Sequence[_Pair], sessions: Sequence[_Interval], split: timedelta
) -> list[_OpenLeg]:
    """Steps 4 and 5: walk the odometer pairs and cut legs."""

    legs: list[_OpenLeg] = []
    cur: _OpenLeg | None = None
    run_start: datetime | None = None  # first reading of the current stationary run

    def close() -> None:
        nonlocal cur
        if cur is not None:
            cur.closed = True
            legs.append(cur)
            cur = None

    for pr in pairs:
        p, q = pr.p, pr.q
        charging = _overlaps(sessions, p.t, q.t)
        if not pr.moving:
            if run_start is None:
                run_start = p.t
            if cur is not None and (cur.sealed or charging or q.t - run_start >= split):
                close()
            continue
        run_start = None
        if charging:
            # Can't tell how much was driven before and after the charge.
            close()
            cur = _OpenLeg(p.t, q.t, q.t, p.v, q.v, sealed=True)
            continue
        if q.t - p.t > split:
            close()
            cur = _OpenLeg(p.t, q.t, q.t, p.v, q.v)
            continue
        if cur is not None and not cur.sealed:
            cur.end_time = q.t
            cur.odometer_end_km = q.v
            continue
        close()
        cur = _OpenLeg(p.t, p.t, q.t, p.v, q.v)

    if cur is not None:
        # A charging session starting at or after the last reading closes it too.
        cur.closed = any(s.start >= cur.end_time for s in sessions)
        legs.append(cur)
    return legs


@dataclass(frozen=True)
class _Soc:
    times: list[datetime]
    values: list[float]

    def latest_at_or_before(self, t: datetime) -> int | None:
        i = bisect_right(self.times, t) - 1
        return i if i >= 0 else None

    def earliest_at_or_after(self, t: datetime) -> int | None:
        i = bisect_left(self.times, t)
        return i if i < len(self.times) else None


def _soc_series(readings: Sequence[Reading]) -> _Soc:
    # After cleaning, two different SoC values can share a timestamp; keep the last.
    times: list[datetime] = []
    values: list[float] = []
    for r in readings:
        if r.signal != "soc_percent":
            continue
        if times and times[-1] == r.recorded_at:
            values[-1] = float(r.value)
            continue
        times.append(r.recorded_at)
        values.append(float(r.value))
    return _Soc(times, values)


def _finish_leg(
    leg: _OpenLeg,
    soc: _Soc,
    sessions: Sequence[_Interval],
    regressions: Sequence[datetime],
    usable_capacity_kwh: float | None,
    settings: InferenceSettings,
    *,
    soc_not_before: datetime | None,
    soc_not_after: datetime | None,
) -> InferredLeg:
    """Step 7: SoC, energy and quality (D8).

    ``soc_not_before`` / ``soc_not_after`` bound the SoC endpoints so the window
    from the start reading to the end reading never reaches into another driving
    leg or a charging session (the previous leg's end or last charge end; the next
    leg's start or next charge start). A reading outside the bounds counts as
    missing (``soc_missing``).
    """

    reasons: set[QualityReason] = set()

    i_start = soc.latest_at_or_before(leg.start_time)
    if i_start is not None and soc_not_before is not None and soc.times[i_start] < soc_not_before:
        i_start = None
    i_end = soc.earliest_at_or_after(leg.end_time)
    if i_end is not None and soc_not_after is not None and soc.times[i_end] > soc_not_after:
        i_end = None
    if i_end is None:
        # Nothing usable at or after the end: the latest reading inside the leg that
        # is later than every reading before start_time_latest.
        j = soc.latest_at_or_before(leg.end_time)
        if j is not None and soc.times[j] > leg.start_time_latest:
            i_end = j

    increased = _overlaps(sessions, leg.start_time, leg.end_time)
    lo = i_start if i_start is not None else bisect_left(soc.times, leg.start_time)
    hi = i_end if i_end is not None else bisect_right(soc.times, leg.end_time) - 1
    lowest = math.inf
    for k in range(lo, hi + 1):
        value = soc.values[k]
        if value > lowest + settings.soc_increase_tolerance_points:
            increased = True
        lowest = min(lowest, value)
    if increased:
        reasons.add("soc_increased")

    delta: float | None = None
    start_age = end_age = timedelta(0)
    if i_start is None or i_end is None:
        reasons.add("soc_missing")
    else:
        delta = soc.values[i_start] - soc.values[i_end]
        start_age = leg.start_time_latest - soc.times[i_start]
        end_age = abs(soc.times[i_end] - leg.end_time)
        if delta < settings.high_min_soc_delta:
            reasons.add("soc_delta_small")
        if max(start_age, end_age) > timedelta(minutes=settings.high_max_soc_age_minutes):
            reasons.add("soc_reading_stale")
    if usable_capacity_kwh is None:
        reasons.add("capacity_unknown")
    exact = leg.start_time == leg.start_time_latest
    if not exact:
        reasons.add("start_time_bounded")
    if any(leg.start_time <= t <= leg.end_time for t in regressions):
        reasons.add("odometer_regression")

    energy: float | None = None
    uncertainty: float | None = None
    if delta is not None and not increased and usable_capacity_kwh is not None:
        energy = round(delta / 100 * usable_capacity_kwh, 3)
        uncertainty = round(usable_capacity_kwh * settings.soc_resolution_points / 100, 3)

    flag: QualityFlag = "low"
    if energy is not None and delta is not None:
        oldest = max(start_age, end_age)
        if (
            delta >= settings.high_min_soc_delta
            and oldest <= timedelta(minutes=settings.high_max_soc_age_minutes)
            and exact
            and "odometer_regression" not in reasons
        ):
            flag = "high"
        elif delta >= settings.medium_min_soc_delta and oldest <= timedelta(
            minutes=settings.medium_max_soc_age_minutes
        ):
            flag = "medium"

    return InferredLeg(
        start_time=leg.start_time,
        start_time_latest=leg.start_time_latest,
        end_time=leg.end_time,
        odometer_start_km=leg.odometer_start_km,
        odometer_end_km=leg.odometer_end_km,
        distance_km=round(leg.odometer_end_km - leg.odometer_start_km, 2),
        soc_start_percent=soc.values[i_start] if i_start is not None else None,
        soc_start_recorded_at=soc.times[i_start] if i_start is not None else None,
        soc_end_percent=soc.values[i_end] if i_end is not None else None,
        soc_end_recorded_at=soc.times[i_end] if i_end is not None else None,
        energy_kwh=energy,
        energy_kwh_uncertainty=uncertainty,
        quality_flag=flag,
        quality_reasons=tuple(r for r in _REASON_ORDER if r in reasons),
        closed=leg.closed,
    )


# --------------------------------------------------------------------------- public API


def infer_legs(
    readings: Iterable[Reading],
    *,
    vehicle_is_simulated: bool,
    usable_capacity_kwh: float | None,
    now: datetime,
    settings: InferenceSettings = InferenceSettings(),
) -> InferenceResult:
    """Cut a vehicle's readings into driving legs (phase5_plan §4.1).

    ``now`` must be timezone-aware. The legs themselves don't depend on it
    (``closed`` is decided by the readings alone); use :func:`is_in_progress`
    for D14.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    cleaned, dropped_simulated, dropped_duplicates = _clean(readings, vehicle_is_simulated)
    points, regressions = _odometer_points(cleaned, settings.min_move_km)
    pairs = [_Pair(p, q, moving=q.v - p.v >= settings.min_move_km) for p, q in pairwise(points)]
    intervals = _charging_intervals(cleaned, pairs)
    split = timedelta(minutes=settings.stop_split_minutes)
    soc = _soc_series(cleaned)

    legs: list[InferredLeg] = []
    dropped_short = 0
    # Bounds come from every leg, short ones included: a parking move is still driving.
    raw_legs = _build_legs(pairs, intervals, split)
    for k, raw in enumerate(raw_legs):
        if round(raw.odometer_end_km - raw.odometer_start_km, 2) < settings.min_leg_km:
            dropped_short += 1
            continue
        lower = [raw_legs[k - 1].end_time] if k > 0 else []
        lower += [s.end for s in intervals if s.end is not None and s.end <= raw.start_time]
        upper = [raw_legs[k + 1].start_time] if k + 1 < len(raw_legs) else []
        upper += [s.start for s in intervals if s.start >= raw.end_time]
        legs.append(
            _finish_leg(
                raw,
                soc,
                intervals,
                regressions,
                usable_capacity_kwh,
                settings,
                soc_not_before=max(lower, default=None),
                soc_not_after=min(upper, default=None),
            )
        )

    sessions = []
    for s in intervals:
        i_start = soc.latest_at_or_before(s.start)
        i_end = soc.latest_at_or_before(s.end) if s.end is not None else None
        sessions.append(
            ChargingSession(
                start=s.start,
                end=s.end,
                soc_start_percent=soc.values[i_start] if i_start is not None else None,
                soc_end_percent=soc.values[i_end] if i_end is not None else None,
            )
        )

    return InferenceResult(
        legs=tuple(legs),
        charging_sessions=tuple(sessions),
        dropped_simulated=dropped_simulated,
        dropped_duplicates=dropped_duplicates,
        dropped_odometer_regressions=len(regressions),
        dropped_short_legs=dropped_short,
    )


def is_in_progress(
    leg: InferredLeg,
    *,
    now: datetime,
    settings: InferenceSettings = InferenceSettings(),
) -> bool:
    """D14: nothing has closed the leg and its last reading is under ``stop_split`` old."""

    return not leg.closed and now - leg.end_time < timedelta(minutes=settings.stop_split_minutes)
