"""Deterministic energy model (phase4_plan.md §3 F2, §4.2; ADR 0004).

Pure functions: no database, no HTTP. The optimizer (F3) and the planner
service (F4) build an ``EnergyVehicle`` from the vehicle's specs and call these.
"""

from app.modules.energy.charging import (
    charging_minutes,
    cumulative_charge_minutes,
    max_charge_soc,
)
from app.modules.energy.model import arrival_soc, segment_energy_kwh, soc_used
from app.modules.energy.types import CurvePoint, EnergyVehicle

__all__ = [
    "CurvePoint",
    "EnergyVehicle",
    "arrival_soc",
    "charging_minutes",
    "cumulative_charge_minutes",
    "max_charge_soc",
    "segment_energy_kwh",
    "soc_used",
]
