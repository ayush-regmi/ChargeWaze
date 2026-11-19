"""Value types shared by the charging optimizer (F3) and ``POST /routes/plan`` (F4).

Signatures from ``docs/phase4_plan.md`` §4.1. Pure dataclasses, no Pydantic:
F4 maps them to and from the API schemas. SoC is a fraction in ``[0, 1]``,
distances are km, power is kW, energy is kWh, time is minutes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from app.modules.energy.types import EnergyVehicle

InfeasibleCode = Literal[
    "STARTING_SOC_TOO_LOW",
    "RESERVE_SOC_UNREACHABLE",
    "NO_COMPATIBLE_CHARGER_IN_RANGE",
    "DESTINATION_UNREACHABLE",
    "NO_DRIVING_ROUTE",  # set by F4 only, never by the optimizer
]


@dataclass(frozen=True)
class RouteProfile:
    distance_km: float
    duration_minutes: float  # Mapbox duration; D13 linear interpolation


@dataclass(frozen=True)
class CandidateStation:
    station_id: uuid.UUID  # charging_stations.id (P3-F5 upsert)
    name: str
    network: str | None
    connector: str  # NACS | CCS1 | J1772 | CHAdeMO, the type used (D12)
    power_kw: float  # known, > 0 (D12)
    distance_along_route_km: float
    distance_from_route_km: float
    lat: float
    lon: float


@dataclass(frozen=True)
class PlannerSettings:
    reserve_soc: float
    charge_to_soc: float  # min(preferred_charge_to, vehicle max_charge_soc)
    stop_overhead_minutes: float
    detour_road_factor: float
    detour_speed_kmh: float
    soc_grid_step: float


@dataclass(frozen=True)
class PlannedStop:
    station: CandidateStation
    arrival_soc: float
    departure_soc: float
    charging_minutes: float
    detour_km: float
    detour_minutes: float


@dataclass(frozen=True)
class FeasiblePlan:
    stops: tuple[PlannedStop, ...]
    arrival_soc: float
    drive_minutes: float
    charging_minutes: float
    detour_minutes: float
    overhead_minutes: float
    total_minutes: float
    energy_kwh: float  # route + detours


@dataclass(frozen=True)
class LimitingSegment:
    from_km: float  # along the route; F4 converts to lat/lon
    to_km: float
    required_soc: float  # SoC needed at from_km to reach to_km with reserve
    available_soc: float  # best SoC the plan can have at from_km


@dataclass(frozen=True)
class InfeasiblePlan:
    code: InfeasibleCode
    message: str
    limiting_segment: LimitingSegment | None


PlanResult = FeasiblePlan | InfeasiblePlan

__all__ = [
    "CandidateStation",
    "EnergyVehicle",
    "FeasiblePlan",
    "InfeasibleCode",
    "InfeasiblePlan",
    "LimitingSegment",
    "PlanResult",
    "PlannedStop",
    "PlannerSettings",
    "RouteProfile",
]
