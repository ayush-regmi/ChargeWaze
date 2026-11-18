"""Fixtures for the pure energy-model tests (no database)."""

from __future__ import annotations

import pytest

from app.modules.energy import CurvePoint, EnergyVehicle
from app.modules.vehicles.spec_catalog import lookup_spec


def bz_vehicle() -> EnergyVehicle:
    """The 2026 bZ XLE AWD built from the real catalog entry (phase4_plan.md §2.3)."""

    entry = lookup_spec("TOYOTA", "bZ XLE AWD", 2026)
    assert entry is not None
    assert entry.usable_capacity_kwh is not None
    assert entry.baseline_kwh_per_km is not None
    return EnergyVehicle(
        usable_capacity_kwh=float(entry.usable_capacity_kwh),
        baseline_kwh_per_km=float(entry.baseline_kwh_per_km),
        charging_curve=tuple(
            CurvePoint(soc=p["soc"], power_kw=p["power_kw"]) for p in entry.charging_curve
        ),
    )


@pytest.fixture
def bz() -> EnergyVehicle:
    return bz_vehicle()
