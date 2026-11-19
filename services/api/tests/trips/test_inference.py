"""Trip inference engine (phase5_plan.md §3 F2, §4.1, §5). Pure: no database."""

from __future__ import annotations

import itertools
import json
import random
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.modules.trips.inference import (
    ChargingSession,
    InferenceResult,
    InferenceSettings,
    InferredLeg,
    Reading,
    SignalKind,
    infer_legs,
    is_in_progress,
)

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "telemetry"
EXAMPLES = sorted(p.stem for p in FIXTURES.glob("example*.json"))
BZ_CAPACITY = 71.0
T0 = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- helpers


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def _readings(data: dict[str, Any]) -> list[Reading]:
    return [
        Reading(
            recorded_at=_ts(r["recorded_at"]),
            signal=r["signal"],
            value=r["value"],
            is_simulated=r["is_simulated"],
        )
        for r in data["readings"]
    ]


def _run(data: dict[str, Any], readings: list[Reading] | None = None) -> InferenceResult:
    inputs = data["inputs"]
    return infer_legs(
        readings if readings is not None else _readings(data),
        vehicle_is_simulated=inputs["vehicle_is_simulated"],
        usable_capacity_kwh=inputs["usable_capacity_kwh"],
        now=_ts(inputs["now"]),
    )


def _leg_json(leg: InferredLeg) -> dict[str, Any]:
    out = asdict(leg)
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out["quality_reasons"] = list(leg.quality_reasons)
    return out


def _session_json(session: ChargingSession) -> dict[str, Any]:
    out = asdict(session)
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def _series(
    rows: list[tuple[int, float | None, float | None, float | None]],
    *,
    start: datetime = T0,
    simulated: bool = False,
) -> list[Reading]:
    """``(minutes after start, odometer, soc, is_charging)``; ``None`` = not reported."""

    out: list[Reading] = []
    for minutes, odo, soc, chg in rows:
        t = start + timedelta(minutes=minutes)
        values: list[tuple[SignalKind, float | None]] = [
            ("odometer_km", odo),
            ("soc_percent", soc),
            ("is_charging", chg),
        ]
        out += [Reading(t, sig, v, simulated) for sig, v in values if v is not None]
    return out


def _infer(
    readings: list[Reading],
    *,
    capacity: float | None = BZ_CAPACITY,
    now: datetime | None = None,
    simulated: bool = False,
    settings: InferenceSettings | None = None,
) -> InferenceResult:
    return infer_legs(
        readings,
        vehicle_is_simulated=simulated,
        usable_capacity_kwh=capacity,
        now=now or T0 + timedelta(days=1),
        settings=settings or InferenceSettings(),
    )


# --------------------------------------------------------------------------- §5 examples


def test_all_five_examples_have_fixtures() -> None:
    assert len(EXAMPLES) == 5


@pytest.mark.parametrize("name", EXAMPLES)
def test_worked_example_matches_exactly(name: str) -> None:
    data = _load(name)
    expected = data["expected"]
    result = _run(data)

    assert [_leg_json(leg) for leg in result.legs] == expected["legs"]
    assert [_session_json(s) for s in result.charging_sessions] == expected["charging_sessions"]
    assert result.dropped_simulated == expected["dropped_simulated"]
    assert result.dropped_duplicates == expected["dropped_duplicates"]
    assert result.dropped_odometer_regressions == expected["dropped_odometer_regressions"]
    assert result.dropped_short_legs == expected["dropped_short_legs"]


@pytest.mark.parametrize("name", EXAMPLES)
@pytest.mark.parametrize("seed", range(5))
def test_any_input_order_gives_the_same_result(name: str, seed: int) -> None:
    data = _load(name)
    readings = _readings(data)
    shuffled = readings[:]
    random.Random(seed).shuffle(shuffled)
    assert _run(data, shuffled) == _run(data, readings)


def test_example1_numbers() -> None:
    (leg,) = _run(_load("example1_dense_commute")).legs
    assert leg.distance_km == 62.00
    assert leg.energy_kwh == 9.940
    assert leg.energy_kwh_uncertainty == 0.710
    assert leg.quality_flag == "high"
    assert leg.quality_reasons == ()
    assert leg.closed is True


def test_example2_open_leg_is_not_in_progress_after_the_split() -> None:
    data = _load("example2_key_off_only")
    a, b = _run(data).legs
    now = _ts(data["inputs"]["now"])
    assert (a.closed, b.closed) == (True, False)
    assert not is_in_progress(b, now=now)
    # Ten minutes after its last reading it would still be in progress (D14).
    assert is_in_progress(b, now=b.end_time + timedelta(minutes=10))
    assert not is_in_progress(a, now=a.end_time + timedelta(minutes=10))


def test_example3_charging_is_never_driving_energy() -> None:
    result = _run(_load("example3_road_trip_dc_stop"))
    leg1, leg2 = result.legs
    (session,) = result.charging_sessions
    for leg in result.legs:
        assert not (session.start < leg.end_time and session.end and session.end > leg.start_time)
    assert leg1.end_time < session.start
    assert session.end is not None and session.end < leg2.start_time
    assert round(leg1.energy_kwh or 0, 3) + round(leg2.energy_kwh or 0, 3) == pytest.approx(80.23)


def test_example4_received_order_is_scrambled() -> None:
    data = _load("example4_gaps_out_of_order")
    times = [r["recorded_at"] for r in data["readings"]]
    assert times != sorted(times)


def test_example5_without_the_filter_would_make_a_400_km_trip() -> None:
    data = _load("example5_simulated_no_capacity")
    as_real = [replace(r, is_simulated=False) for r in _readings(data)]
    result = _infer(as_real, capacity=None, now=_ts(data["inputs"]["now"]))
    assert result.dropped_simulated == 0
    assert max(leg.distance_km for leg in result.legs) > 300


# --------------------------------------------------------------------------- cleaning


def test_empty_input_gives_no_legs() -> None:
    result = _infer([])
    assert result == InferenceResult((), (), 0, 0, 0, 0)


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        infer_legs(
            [], vehicle_is_simulated=False, usable_capacity_kwh=None, now=datetime(2026, 1, 1)
        )


def test_exact_duplicates_are_collapsed_but_different_values_are_kept() -> None:
    rows = _series([(0, 100.0, 80.0, None), (10, 120.0, 75.0, None), (40, 120.0, 75.0, None)])
    dup = [*rows, rows[0], rows[2], rows[2]]
    result = _infer(dup)
    assert result.dropped_duplicates == 3
    assert result.legs == _infer(rows).legs


def test_none_and_nan_values_are_dropped() -> None:
    rows = _series([(0, 100.0, 80.0, None), (10, 120.0, 75.0, None), (40, 120.0, 75.0, None)])
    bad = [
        Reading(T0 + timedelta(minutes=5), "soc_percent", None, False),  # type: ignore[arg-type]
        Reading(T0 + timedelta(minutes=6), "odometer_km", float("nan"), False),
    ]
    result = _infer(rows + bad)
    assert result.legs == _infer(rows).legs
    assert result.dropped_duplicates == 0


def test_simulated_rows_are_dropped_and_counted_both_ways() -> None:
    real = _series([(0, 100.0, 80.0, None), (10, 120.0, 75.0, None), (40, 120.0, 75.0, None)])
    sim = _series([(5, 900.0, 10.0, 1.0)], simulated=True)
    result = _infer(real + sim)
    assert result.dropped_simulated == 3
    assert result.charging_sessions == ()
    assert [leg.distance_km for leg in result.legs] == [20.0]

    # A simulated vehicle keeps only its simulated readings.
    sim_vehicle = _infer(real + sim, simulated=True)
    assert sim_vehicle.dropped_simulated == len(real)
    assert sim_vehicle.legs == ()


def test_small_odometer_dip_is_noise_not_a_regression() -> None:
    rows = _series(
        [(0, 100.0, 80.0, None), (10, 120.0, 75.0, None), (12, 119.95, None, None),
         (15, 125.0, 74.0, None), (45, 125.0, 74.0, None)]
    )  # fmt: skip
    result = _infer(rows)
    assert result.dropped_odometer_regressions == 0
    (leg,) = result.legs
    assert "odometer_regression" not in leg.quality_reasons


def test_odometer_regression_is_dropped_flagged_and_caps_quality_at_medium() -> None:
    rows = _series(
        [(0, 100.0, 80.0, None), (5, 110.0, 76.0, None), (8, 105.0, None, None),
         (10, 120.0, 68.0, None), (40, 120.0, 68.0, None)]
    )  # fmt: skip
    result = _infer(rows)
    assert result.dropped_odometer_regressions == 1
    (leg,) = result.legs
    assert leg.distance_km == 20.0
    assert leg.quality_reasons == ("odometer_regression",)
    assert leg.quality_flag == "medium"  # would be high without the regression


# --------------------------------------------------------------------------- splitting


def test_stop_shorter_than_split_does_not_split() -> None:
    rows = _series(
        [(0, 100.0, 80.0, None), (10, 110.0, 78.0, None), (25, 110.0, 78.0, None),
         (29, 115.0, 77.0, None), (60, 115.0, 77.0, None)]
    )  # fmt: skip
    (leg,) = _infer(rows).legs
    assert (leg.odometer_start_km, leg.odometer_end_km) == (100.0, 115.0)


def test_stop_of_split_minutes_closes_the_leg_at_the_first_stationary_reading() -> None:
    rows = _series(
        [(0, 100.0, 80.0, None), (10, 110.0, 78.0, None), (20, 110.0, 78.0, None),
         (30, 110.0, 78.0, None), (35, 120.0, 76.0, None), (70, 120.0, 76.0, None)]
    )  # fmt: skip
    first, second = _infer(rows).legs
    assert first.end_time == T0 + timedelta(minutes=10)
    assert first.closed
    assert second.start_time == second.start_time_latest == T0 + timedelta(minutes=30)


def test_charging_session_always_splits_even_with_no_odometer_reading_during_it() -> None:
    rows = _series(
        [(0, 100.0, 80.0, 0.0), (10, 120.0, 70.0, None), (11, None, 70.0, 1.0),
         (18, None, 85.0, 0.0), (19, 120.0, 85.0, None), (25, 130.0, 81.0, None),
         (60, 130.0, 81.0, None)]
    )  # fmt: skip
    result = _infer(rows)
    first, second = result.legs
    assert first.end_time == T0 + timedelta(minutes=10)
    assert second.start_time == T0 + timedelta(minutes=19)
    assert first.energy_kwh == round(10 / 100 * BZ_CAPACITY, 3)
    assert second.energy_kwh == round(4 / 100 * BZ_CAPACITY, 3)
    assert result.charging_sessions == (
        ChargingSession(T0 + timedelta(minutes=11), T0 + timedelta(minutes=18), 70.0, 85.0),
    )


def test_charging_starting_after_the_last_reading_closes_the_open_leg() -> None:
    rows = _series([(0, 100.0, 80.0, None), (10, 120.0, 70.0, None), (12, None, 70.0, 1.0)])
    (leg,) = _infer(rows).legs
    assert leg.closed
    (session,) = _infer(rows).charging_sessions
    assert session.end is None


def test_moving_pair_with_charging_inside_is_its_own_leg_without_energy() -> None:
    rows = _series(
        [(0, 100.0, 80.0, 0.0), (10, 110.0, 76.0, None), (12, None, 76.0, 1.0),
         (20, None, 90.0, 0.0), (25, 115.0, 89.0, None), (30, 125.0, 85.0, None),
         (70, 125.0, 85.0, None)]
    )  # fmt: skip
    before, charged, after = _infer(rows).legs
    assert (before.odometer_start_km, before.odometer_end_km) == (100.0, 110.0)
    assert before.energy_kwh is not None

    assert charged.start_time == T0 + timedelta(minutes=10)
    assert charged.start_time_latest == charged.end_time == T0 + timedelta(minutes=25)
    assert charged.energy_kwh is None
    assert "soc_increased" in charged.quality_reasons
    assert "start_time_bounded" in charged.quality_reasons
    assert charged.closed

    assert after.start_time == after.start_time_latest == T0 + timedelta(minutes=25)
    assert (after.odometer_start_km, after.odometer_end_km) == (115.0, 125.0)
    assert after.energy_kwh == round(4 / 100 * BZ_CAPACITY, 3)


def test_lost_charging_stop_event_is_ended_by_motion() -> None:
    # Plugged in overnight; the "not charging" event never arrives.
    rows = _series(
        [(-600, 100.0, 40.0, 1.0), (-60, 100.0, 90.0, None), (0, 100.0, 90.0, None),
         (10, 110.0, 87.0, None), (20, 125.0, 83.0, None), (30, 140.0, 79.0, None),
         (70, 140.0, 79.0, None)]
    )  # fmt: skip
    result = _infer(rows)
    (session,) = result.charging_sessions
    assert session.start == T0 - timedelta(minutes=600)
    assert session.end == T0 + timedelta(minutes=10)  # upper bound: first moving reading
    charged, clean = result.legs
    assert charged.energy_kwh is None
    assert (clean.odometer_start_km, clean.odometer_end_km) == (110.0, 140.0)
    assert clean.energy_kwh == round(8 / 100 * BZ_CAPACITY, 3)


def test_long_gap_while_moving_gives_a_bounded_start() -> None:
    rows = _series([(0, 100.0, 80.0, None), (90, 160.0, 66.0, None), (130, 160.0, 66.0, None)])
    (leg,) = _infer(rows).legs
    assert leg.start_time == T0
    assert leg.start_time_latest == leg.end_time == T0 + timedelta(minutes=90)
    assert "start_time_bounded" in leg.quality_reasons
    assert leg.quality_flag != "high"


def test_short_legs_are_dropped_and_counted() -> None:
    rows = _series([(0, 100.0, 80.0, None), (2, 100.4, 80.0, None), (40, 100.4, 80.0, None)])
    result = _infer(rows)
    assert result.legs == ()
    assert result.dropped_short_legs == 1


def test_open_leg_is_reported_with_closed_false() -> None:
    rows = _series([(0, 100.0, 80.0, None), (5, 104.0, 79.0, None), (10, 108.0, 77.0, None)])
    now = T0 + timedelta(minutes=15)
    (leg,) = _infer(rows, now=now).legs
    assert leg.closed is False
    assert is_in_progress(leg, now=now)
    assert not is_in_progress(leg, now=T0 + timedelta(minutes=30))


# --------------------------------------------------------------------------- SoC and quality


def test_soc_rise_inside_a_leg_is_caught_against_the_lowest_reading_so_far() -> None:
    # 80 → 20 → 60 → 50: caught at 60 even though 60 < 80.
    rows = _series(
        [(0, 100.0, 80.0, None), (5, 110.0, 20.0, None), (10, 120.0, 60.0, None),
         (15, 130.0, 50.0, None), (50, 130.0, 50.0, None)]
    )  # fmt: skip
    (leg,) = _infer(rows).legs
    assert leg.energy_kwh is None
    assert "soc_increased" in leg.quality_reasons
    assert leg.quality_flag == "low"


def test_soc_rise_within_tolerance_is_jitter() -> None:
    rows = _series(
        [(0, 100.0, 80.0, None), (5, 110.0, 70.0, None), (10, 120.0, 71.0, None),
         (15, 130.0, 66.0, None), (50, 130.0, 66.0, None)]
    )  # fmt: skip
    (leg,) = _infer(rows).legs
    assert leg.energy_kwh == round(14 / 100 * BZ_CAPACITY, 3)
    assert leg.quality_flag == "high"


def test_missing_soc_means_no_energy() -> None:
    rows = _series([(0, 100.0, None, None), (10, 120.0, None, None), (40, 120.0, None, None)])
    (leg,) = _infer(rows).legs
    assert leg.soc_start_percent is None and leg.soc_end_percent is None
    assert leg.energy_kwh is None and leg.energy_kwh_uncertainty is None
    assert leg.quality_reasons == ("soc_missing",)
    assert leg.quality_flag == "low"


def test_soc_end_falls_back_to_a_reading_inside_the_leg() -> None:
    rows = _series(
        [(0, 100.0, 80.0, None), (5, 110.0, None, None), (10, 120.0, 70.0, None),
         (14, 130.0, None, None)]
    )  # fmt: skip
    (leg,) = _infer(rows).legs
    assert leg.soc_end_percent == 70.0
    assert leg.soc_end_recorded_at == T0 + timedelta(minutes=10)
    assert leg.energy_kwh == round(10 / 100 * BZ_CAPACITY, 3)
    assert leg.quality_flag == "high"  # end age 4 min


def test_soc_end_fallback_never_reuses_the_start_reading() -> None:
    rows = _series([(0, 100.0, 80.0, None), (10, 120.0, None, None)])
    (leg,) = _infer(rows).legs
    assert leg.soc_end_percent is None
    assert leg.quality_reasons == ("soc_missing",)


def test_two_legs_never_share_one_soc_window() -> None:
    # PR #39 review (Codex P1): SoC only at min 0 (80 %) and min 50 (60 %). Before the
    # fix both legs took 80 → 60 and reported 14.2 kWh each (28.4 against 14.2 measured).
    rows = _series(
        [(0, 100.0, 80.0, None), (10, 120.0, None, None), (40, 120.0, None, None),
         (50, 140.0, 60.0, None), (80, 140.0, None, None)]
    )  # fmt: skip
    first, second = _infer(rows).legs
    assert (first.end_time, second.start_time) == (
        T0 + timedelta(minutes=10),
        T0 + timedelta(minutes=40),
    )
    windows = [(leg.soc_start_recorded_at, leg.soc_end_recorded_at) for leg in (first, second)]
    assert windows[0] != windows[1]
    assert first.energy_kwh is None or second.energy_kwh is None
    for leg in (first, second):
        assert leg.energy_kwh is None
        assert leg.quality_reasons[0] == "soc_missing"
        assert leg.quality_flag == "low"
    assert first.soc_start_percent == 80.0 and first.soc_end_percent is None
    assert second.soc_start_percent is None and second.soc_end_percent == 60.0


def test_soc_end_is_never_read_across_a_later_charging_session() -> None:
    # Leg ends at min 10 with no SoC reading; the car then charges (min 12 to 30) and the next
    # SoC reading (90 %, min 35) is after the charge. It must not be the leg's end SoC.
    rows = _series(
        [(0, 100.0, 80.0, 0.0), (10, 120.0, None, None), (12, None, None, 1.0),
         (30, None, None, 0.0), (35, 120.0, 90.0, None)]
    )  # fmt: skip
    result = _infer(rows)
    (leg,) = result.legs
    assert len(result.charging_sessions) == 1
    assert leg.closed
    assert leg.soc_end_percent is None
    assert leg.energy_kwh is None
    assert leg.quality_reasons == ("soc_missing",)


def test_soc_start_is_never_read_across_an_earlier_charging_session() -> None:
    # The last SoC reading before the leg (40 %) predates a charge (min -60 to -10).
    rows = _series(
        [(-120, 100.0, 40.0, 0.0), (-60, None, None, 1.0), (-10, None, None, 0.0),
         (0, 100.0, None, None), (10, 120.0, 85.0, None), (40, 120.0, 85.0, None)]
    )  # fmt: skip
    (leg,) = _infer(rows).legs
    assert leg.soc_start_percent is None
    assert leg.energy_kwh is None
    assert "soc_missing" in leg.quality_reasons
    assert "soc_increased" not in leg.quality_reasons


def test_dropped_short_leg_still_bounds_its_neighbours() -> None:
    # A 0.3 km parking move between two SoC readings is still driving.
    rows = _series(
        [(0, 100.0, 80.0, None), (10, 120.0, None, None), (40, 120.0, None, None),
         (42, 120.3, None, None), (80, 120.3, 66.0, None)]
    )  # fmt: skip
    result = _infer(rows)
    assert result.dropped_short_legs == 1
    (leg,) = result.legs
    assert leg.soc_end_percent is None
    assert leg.energy_kwh is None


def test_medium_needs_four_points_and_fresh_enough_readings() -> None:
    def leg_for(soc_end: float, soc_start_age: int) -> InferredLeg:
        rows = _series(
            [(-soc_start_age, None, 80.0, None), (0, 100.0, None, None),
             (10, 140.0, soc_end, None), (40, 140.0, soc_end, None)]
        )  # fmt: skip
        (leg,) = _infer(rows).legs
        return leg

    assert leg_for(76.0, 0).quality_flag == "medium"
    assert leg_for(77.0, 0).quality_flag == "low"
    assert leg_for(70.0, 0).quality_flag == "high"
    stale = leg_for(70.0, 30)
    assert stale.quality_flag == "medium"
    assert stale.quality_reasons == ("soc_reading_stale",)
    assert leg_for(70.0, 361).quality_flag == "low"


def test_settings_change_the_thresholds() -> None:
    rows = _series(
        [(0, 100.0, 80.0, None), (10, 110.0, 78.0, None), (25, 110.0, 78.0, None),
         (29, 115.0, 77.0, None), (60, 115.0, 77.0, None)]
    )  # fmt: skip
    legs = _infer(rows, settings=InferenceSettings(stop_split_minutes=10)).legs
    assert len(legs) == 2
    assert _infer(rows, settings=InferenceSettings(min_leg_km=20)).legs == ()


def test_uncertainty_uses_resolution_setting() -> None:
    rows = _series([(0, 100.0, 80.0, None), (10, 120.0, 70.0, None), (40, 120.0, 70.0, None)])
    (leg,) = _infer(rows, settings=InferenceSettings(soc_resolution_points=2)).legs
    assert leg.energy_kwh_uncertainty == 1.42


# --------------------------------------------------------------------------- properties


def _random_series(rng: random.Random) -> list[Reading]:
    t = T0
    odo = 10_000.0
    soc = 90.0
    charging = False
    out: list[Reading] = []
    for _ in range(rng.randint(5, 80)):
        t += timedelta(minutes=rng.choice([1, 3, 5, 10, 15, 25, 45, 120, 600]))
        roll = rng.random()
        if charging:
            soc = min(100.0, soc + rng.randint(0, 15))
            if roll < 0.4:
                charging = False
                out.append(Reading(t, "is_charging", 0.0, False))
        elif roll < 0.08:
            charging = True
            out.append(Reading(t, "is_charging", 1.0, False))
        elif roll < 0.6:
            km = round(rng.uniform(0, 30), 2)
            odo = round(odo + km, 2)
            soc = max(0.0, soc - round(km * 0.3))
        if rng.random() < 0.9:
            out.append(Reading(t, "odometer_km", odo, False))
        if rng.random() < 0.7:
            out.append(Reading(t, "soc_percent", soc, False))
    return out


@pytest.mark.parametrize("seed", range(200))
def test_generated_series_invariants(seed: int) -> None:
    rng = random.Random(seed)
    readings = _random_series(rng)
    result = _infer(readings, now=T0 + timedelta(days=30))
    legs = result.legs

    # Legs are ordered and never overlap.
    for prev, nxt in itertools.pairwise(legs):
        assert prev.end_time <= nxt.start_time
        assert prev.odometer_end_km <= nxt.odometer_start_km
    # Only the last leg can be open.
    assert all(leg.closed for leg in legs[:-1])

    odometers = [r.value for r in readings if r.signal == "odometer_km"]
    total = (max(odometers) - min(odometers)) if odometers else 0.0
    assert sum(leg.distance_km for leg in legs) <= total + 1e-6

    socs = {(r.recorded_at, r.value) for r in readings if r.signal == "soc_percent"}
    for leg in legs:
        assert leg.start_time <= leg.start_time_latest <= leg.end_time
        assert leg.distance_km >= InferenceSettings().min_leg_km
        if leg.soc_start_percent is not None:
            assert (leg.soc_start_recorded_at, leg.soc_start_percent) in socs
        if leg.soc_end_percent is not None:
            assert (leg.soc_end_recorded_at, leg.soc_end_percent) in socs
        if leg.energy_kwh is None:
            assert leg.energy_kwh_uncertainty is None
            continue
        assert leg.soc_start_percent is not None and leg.soc_end_percent is not None
        expected = round((leg.soc_start_percent - leg.soc_end_percent) / 100 * BZ_CAPACITY, 3)
        assert leg.energy_kwh == expected
        # A leg with energy never overlaps a charging session (X2), and nor does the
        # window between its two SoC readings.
        assert leg.soc_start_recorded_at is not None and leg.soc_end_recorded_at is not None
        for s in result.charging_sessions:
            assert not (s.start < leg.end_time and (s.end is None or s.end > leg.start_time))
            assert not (
                s.start < leg.soc_end_recorded_at
                and (s.end is None or s.end > leg.soc_start_recorded_at)
            )

    # Legs with energy never share SoC: each one's window starts where the last ended.
    windows = [
        (leg.soc_start_recorded_at, leg.soc_end_recorded_at)
        for leg in legs
        if leg.energy_kwh is not None
    ]
    for (_, prev_end), (next_start, _) in itertools.pairwise(windows):
        assert prev_end is not None and next_start is not None
        assert prev_end <= next_start

    shuffled = readings[:]
    rng.shuffle(shuffled)
    assert _infer(shuffled, now=T0 + timedelta(days=30)) == result
