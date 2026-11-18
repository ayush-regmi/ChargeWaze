"""Flat-baseline consumption (phase4_plan.md D1): energy is distance x the
vehicle's ``baseline_kwh_per_km``. No speed, elevation, temperature or HVAC
adjustments in v1 (D15), so a plan's ``adjustments_applied`` is always empty.
"""

from __future__ import annotations

import math

from app.modules.energy.types import EnergyVehicle


def segment_energy_kwh(vehicle: EnergyVehicle, distance_km: float) -> float:
    """Energy for driving ``distance_km``. ValueError for a negative or non-finite
    distance."""

    if not (math.isfinite(distance_km) and distance_km >= 0):
        raise ValueError("distance_km must be a finite number >= 0.")
    return distance_km * vehicle.baseline_kwh_per_km


def soc_used(vehicle: EnergyVehicle, distance_km: float) -> float:
    """SoC fraction used for ``distance_km`` (may exceed 1 for long distances)."""

    return segment_energy_kwh(vehicle, distance_km) / vehicle.usable_capacity_kwh


def arrival_soc(vehicle: EnergyVehicle, start_soc: float, distance_km: float) -> float:
    """May be negative; callers compare with the reserve."""

    return start_soc - soc_used(vehicle, distance_km)


__all__ = ["arrival_soc", "segment_energy_kwh", "soc_used"]
