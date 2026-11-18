"""Consumption: energy, SoC used, arrival SoC (phase4_plan.md D1, §4.2)."""

from __future__ import annotations

import math

import pytest

from app.modules.energy import (
    CurvePoint,
    EnergyVehicle,
    arrival_soc,
    segment_energy_kwh,
    soc_used,
)

CURVE = (CurvePoint(0.0, 50.0), CurvePoint(1.0, 50.0))


def test_one_km_of_bz_is_0_282_percent(bz: EnergyVehicle) -> None:
    assert soc_used(bz, 1.0) == pytest.approx(0.0028169, abs=1e-7)


def test_zero_distance_uses_nothing(bz: EnergyVehicle) -> None:
    assert segment_energy_kwh(bz, 0.0) == 0.0
    assert soc_used(bz, 0.0) == 0.0
    assert arrival_soc(bz, 0.42, 0.0) == 0.42


def test_arrival_may_be_negative(bz: EnergyVehicle) -> None:
    # 400 km uses 1.12676 SoC (§5 trip 2): never clamped; callers compare with the reserve.
    assert soc_used(bz, 400.0) == pytest.approx(1.12676, abs=1e-5)
    assert arrival_soc(bz, 0.80, 400.0) == pytest.approx(0.80 - 1.12676, abs=1e-5)


@pytest.mark.parametrize("distance", [-0.1, math.nan, math.inf])
def test_bad_distance_rejected(bz: EnergyVehicle, distance: float) -> None:
    with pytest.raises(ValueError):
        segment_energy_kwh(bz, distance)


def test_energy_is_linear_in_distance(bz: EnergyVehicle) -> None:
    for a in (0.0, 1.5, 37.0, 120.0):
        for b in (0.0, 2.6, 80.0, 400.0):
            assert segment_energy_kwh(bz, a + b) == pytest.approx(
                segment_energy_kwh(bz, a) + segment_energy_kwh(bz, b)
            )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"usable_capacity_kwh": 0.0}, "usable_capacity_kwh"),
        ({"usable_capacity_kwh": math.nan}, "usable_capacity_kwh"),
        ({"baseline_kwh_per_km": -0.2}, "baseline_kwh_per_km"),
        ({"charging_curve": (CurvePoint(0.0, 50.0),)}, "at least 2"),
        ({"charging_curve": (CurvePoint(0.5, 50.0), CurvePoint(0.5, 40.0))}, "increasing"),
        ({"charging_curve": (CurvePoint(0.8, 50.0), CurvePoint(0.2, 40.0))}, "increasing"),
        ({"charging_curve": (CurvePoint(0.0, 50.0), CurvePoint(1.1, 40.0))}, r"\[0, 1\]"),
        ({"charging_curve": (CurvePoint(-0.1, 50.0), CurvePoint(1.0, 40.0))}, r"\[0, 1\]"),
        ({"charging_curve": (CurvePoint(0.0, 50.0), CurvePoint(1.0, 0.0))}, "power_kw"),
    ],
)
def test_vehicle_validation(kwargs: dict[str, object], message: str) -> None:
    fields: dict[str, object] = {
        "usable_capacity_kwh": 71.0,
        "baseline_kwh_per_km": 0.2,
        "charging_curve": CURVE,
    }
    fields.update(kwargs)
    with pytest.raises(ValueError, match=message):
        EnergyVehicle(**fields)  # type: ignore[arg-type]
