"""Value types for the deterministic energy model (phase4_plan.md §4.2).

SoC is a fraction in ``[0, 1]``, power is kW, energy is kWh, distance is km.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CurvePoint:
    """One point of a piecewise-linear charging curve: power *into the battery*
    at that SoC (D15: charging losses aren't modelled)."""

    soc: float
    power_kw: float


@dataclass(frozen=True)
class EnergyVehicle:
    usable_capacity_kwh: float
    baseline_kwh_per_km: float
    charging_curve: tuple[CurvePoint, ...]  # ≥ 2 points, soc strictly increasing, power > 0

    def __post_init__(self) -> None:
        if not _positive(self.usable_capacity_kwh):
            raise ValueError("usable_capacity_kwh must be a finite number > 0.")
        if not _positive(self.baseline_kwh_per_km):
            raise ValueError("baseline_kwh_per_km must be a finite number > 0.")
        curve = self.charging_curve
        if len(curve) < 2:
            raise ValueError("charging_curve needs at least 2 points.")
        for point in curve:
            if not (math.isfinite(point.soc) and 0.0 <= point.soc <= 1.0):
                raise ValueError("charging_curve soc values must be in [0, 1].")
            if not _positive(point.power_kw):
                raise ValueError("charging_curve power_kw values must be > 0.")
        if any(b.soc <= a.soc for a, b in itertools.pairwise(curve)):
            raise ValueError("charging_curve soc values must be strictly increasing.")


def _positive(value: float) -> bool:
    return math.isfinite(value) and value > 0


__all__ = ["CurvePoint", "EnergyVehicle"]
