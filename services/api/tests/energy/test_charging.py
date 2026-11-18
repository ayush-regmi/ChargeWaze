"""Charging time over the piecewise-linear curve (phase4_plan.md §3 F2)."""

from __future__ import annotations

import itertools
import math

import pytest

from app.modules.energy import (
    CurvePoint,
    EnergyVehicle,
    charging_minutes,
    cumulative_charge_minutes,
    max_charge_soc,
)

# Fixed grid instead of Hypothesis (no new dependency): every SoC pair and station
# power below, including values on, between and around the bZ curve points and the
# SoCs where the curve crosses each station power.
SOC_GRID = (0.0, 0.03, 0.1, 0.15, 0.17296, 0.2, 0.3, 0.37, 0.5, 0.55, 0.61, 0.68, 0.8, 0.93, 1.0)
POWER_GRID = (7.2, 15.0, 50.0, 62.5, 100.0, 120.0, 150.0, 250.0, 350.0)
UNCAPPED = 1000.0


def _numeric(vehicle: EnergyVehicle, a: float, b: float, cap: float, n: int = 20000) -> float:
    """Midpoint-rule integral of 60·B / min(P(s), cap): an independent check."""

    curve = vehicle.charging_curve

    def power(s: float) -> float:
        if s <= curve[0].soc:
            return curve[0].power_kw
        for p, q in itertools.pairwise(curve):
            if p.soc <= s <= q.soc:
                return p.power_kw + (q.power_kw - p.power_kw) * (s - p.soc) / (q.soc - p.soc)
        raise AssertionError(s)

    h = (b - a) / n
    return sum(
        60 * vehicle.usable_capacity_kwh * h / min(power(a + (i + 0.5) * h), cap) for i in range(n)
    )


def test_bz_10_to_80_uncapped(bz: EnergyVehicle) -> None:
    # §3 F2 / §2.3: 27.36 min (49.7 kWh at ~109 kW average).
    minutes = charging_minutes(bz, 0.10, 0.80, UNCAPPED)
    assert minutes == pytest.approx(27.36, abs=0.005)
    assert 0.70 * 71.0 / (minutes / 60) == pytest.approx(109, abs=0.5)


def test_bz_20_to_50_at_50_kw(bz: EnergyVehicle) -> None:
    # Station caps the whole range: 60 · 71 · 0.30 / 50.
    assert charging_minutes(bz, 0.20, 0.50, 50.0) == pytest.approx(25.56, abs=1e-9)


def test_station_above_vehicle_peak_changes_nothing(bz: EnergyVehicle) -> None:
    assert charging_minutes(bz, 0.1, 0.8, 150.0) == pytest.approx(
        charging_minutes(bz, 0.1, 0.8, 350.0)
    )


@pytest.mark.parametrize(("a", "b"), [(0.5, 0.5), (0.8, 0.2), (0.0, 0.0), (1.0, 1.0)])
def test_no_charge_is_zero_minutes(bz: EnergyVehicle, a: float, b: float) -> None:
    assert charging_minutes(bz, a, b, 150.0) == 0.0


def test_max_charge_soc_is_last_point(bz: EnergyVehicle) -> None:
    assert max_charge_soc(bz) == 1.0
    capped = EnergyVehicle(71.0, 0.2, (CurvePoint(0.1, 100.0), CurvePoint(0.9, 20.0)))
    assert max_charge_soc(capped) == 0.9


def test_above_max_charge_soc_rejected() -> None:
    vehicle = EnergyVehicle(71.0, 0.2, (CurvePoint(0.1, 100.0), CurvePoint(0.9, 20.0)))
    with pytest.raises(ValueError, match="max_charge_soc"):
        charging_minutes(vehicle, 0.5, 0.95, 150.0)
    with pytest.raises(ValueError, match="max_charge_soc"):
        cumulative_charge_minutes(vehicle, 150.0)(0.95)


@pytest.mark.parametrize(("a", "b"), [(-0.01, 0.5), (0.2, 1.01), (math.nan, 0.5), (0.2, math.inf)])
def test_soc_outside_range_rejected(bz: EnergyVehicle, a: float, b: float) -> None:
    with pytest.raises(ValueError):
        charging_minutes(bz, a, b, 150.0)


@pytest.mark.parametrize("power", [0.0, -50.0, math.nan, math.inf])
def test_bad_station_power_rejected(bz: EnergyVehicle, power: float) -> None:
    with pytest.raises(ValueError, match="station_power_kw"):
        charging_minutes(bz, 0.2, 0.5, power)
    with pytest.raises(ValueError, match="station_power_kw"):
        cumulative_charge_minutes(bz, power)


def test_float_noise_at_bounds_is_clamped(bz: EnergyVehicle) -> None:
    assert charging_minutes(bz, -1e-12, 1.0 + 1e-12, 150.0) == pytest.approx(
        charging_minutes(bz, 0.0, 1.0, 150.0)
    )


def test_first_point_power_holds_below_curve() -> None:
    # Curve starts at 0.10; below it the 0.10 power (100 kW) holds.
    vehicle = EnergyVehicle(60.0, 0.2, (CurvePoint(0.1, 100.0), CurvePoint(0.9, 20.0)))
    assert charging_minutes(vehicle, 0.0, 0.1, 350.0) == pytest.approx(60 * 60 * 0.1 / 100)


@pytest.mark.parametrize("power", POWER_GRID)
def test_matches_numeric_integral(bz: EnergyVehicle, power: float) -> None:
    for a, b in [(0.0, 1.0), (0.03, 0.37), (0.15, 0.93), (0.5, 0.68)]:
        assert charging_minutes(bz, a, b, power) == pytest.approx(
            _numeric(bz, a, b, power), rel=1e-6
        )


def test_monotonic_in_target_soc(bz: EnergyVehicle) -> None:
    for power in POWER_GRID:
        for a in SOC_GRID:
            targets = [b for b in SOC_GRID if b > a]
            minutes = [charging_minutes(bz, a, b, power) for b in targets]
            assert all(m > 0 for m in minutes)
            assert all(x < y for x, y in itertools.pairwise(minutes))


def test_not_faster_at_lower_station_power(bz: EnergyVehicle) -> None:
    for a, b in itertools.combinations(SOC_GRID, 2):
        minutes = [charging_minutes(bz, a, b, power) for power in POWER_GRID]  # ascending power
        assert all(x >= y - 1e-9 for x, y in itertools.pairwise(minutes))


def test_additive(bz: EnergyVehicle) -> None:
    for power in POWER_GRID:
        for a, b, c in itertools.combinations(SOC_GRID, 3):
            assert charging_minutes(bz, a, c, power) == pytest.approx(
                charging_minutes(bz, a, b, power) + charging_minutes(bz, b, c, power),
                abs=1e-9,
            )


def test_cumulative_matches_charging_minutes(bz: EnergyVehicle) -> None:
    for power in POWER_GRID:
        f = cumulative_charge_minutes(bz, power)
        assert f(0.0) == 0.0
        for a, b in itertools.combinations(SOC_GRID, 2):
            assert f(b) - f(a) == pytest.approx(charging_minutes(bz, a, b, power), abs=1e-9)
            assert f(b) == pytest.approx(charging_minutes(bz, 0.0, b, power), abs=1e-9)
