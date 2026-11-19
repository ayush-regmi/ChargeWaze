"""Request/response models for the routing module.

``docs/api-contracts.md`` §3.15 (``POST /routes/directions``) and §3.7
(``POST /routes/plan``, Phase 4 F4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.geo import haversine_km
from app.integrations.mapbox.schemas import NoRouteReason
from app.modules.routing.plan_types import InfeasibleCode
from app.modules.users.schemas import (
    MAX_DETOUR_MINUTES,
    MAX_NETWORK_NAME_LENGTH,
    MAX_PREFERRED_NETWORKS,
    SOC_DECIMAL_PLACES,
)
from app.schemas.geo import Coordinates

MIN_ENDPOINT_SEPARATION_KM = 0.05
MAX_ENDPOINT_LABEL_LENGTH = 200
# Mapbox returns at most three routes (phase3_plan.md D5); a higher index is
# accepted here and answered with ROUTE_INDEX_UNAVAILABLE, like any other miss.
MAX_ROUTE_INDEX = 10


def _check_endpoints_apart(origin: Coordinates, destination: Coordinates) -> None:
    separation = haversine_km(origin.as_tuple(), destination.as_tuple())
    if separation <= MIN_ENDPOINT_SEPARATION_KM:
        raise ValueError("origin and destination must be more than 50 m apart.")


# ---------------------------------------------------------------------------
# §3.15 POST /routes/directions
# ---------------------------------------------------------------------------


class DirectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Coordinates
    destination: Coordinates

    @model_validator(mode="after")
    def _endpoints_apart(self) -> Self:
        _check_endpoints_apart(self.origin, self.destination)
        return self


class DirectionsRouteOut(BaseModel):
    index: int
    geometry_polyline6: str
    distance_m: float
    duration_s: float
    summary: str | None
    countries: list[str]


class DirectionsResponse(BaseModel):
    routes: list[DirectionsRouteOut]
    no_route_reason: NoRouteReason | None


# ---------------------------------------------------------------------------
# §3.7 POST /routes/plan: request
# ---------------------------------------------------------------------------


class PlanEndpoint(Coordinates):
    """A trip endpoint: a point plus an optional display label (stored)."""

    label: str | None = Field(default=None, max_length=MAX_ENDPOINT_LABEL_LENGTH)


class PlanPreferences(BaseModel):
    """Per-request preference overrides. Same rules as ``PATCH /me/preferences``
    (§3.1a); ``reserve_soc < preferred_charge_to`` is checked after merging with
    the stored values, in the planner service."""

    model_config = ConfigDict(extra="forbid")

    reserve_soc: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    preferred_charge_to: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    max_detour_minutes: int | None = Field(default=None, ge=0, le=MAX_DETOUR_MINUTES)
    preferred_networks: list[str] | None = Field(default=None, max_length=MAX_PREFERRED_NETWORKS)

    @field_validator(
        "reserve_soc", "preferred_charge_to", "max_detour_minutes", "preferred_networks"
    )
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Validators don't run for defaults, so this only fires on an explicit null.
        if value is None:
            raise ValueError("may be omitted but not null.")
        return value

    @field_validator("reserve_soc", "preferred_charge_to")
    @classmethod
    def _precision(cls, value: float | None) -> float | None:
        if value is not None and round(value, SOC_DECIMAL_PLACES) != value:
            raise ValueError(f"use at most {SOC_DECIMAL_PLACES} decimal places.")
        return value

    @field_validator("preferred_networks")
    @classmethod
    def _clean_networks(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [name.strip() for name in value]
        if any(not name for name in cleaned):
            raise ValueError("network names must not be empty.")
        if any(len(name) > MAX_NETWORK_NAME_LENGTH for name in cleaned):
            raise ValueError(f"network names must be at most {MAX_NETWORK_NAME_LENGTH} chars.")
        if len({name.casefold() for name in cleaned}) != len(cleaned):
            raise ValueError("network names must be distinct.")
        return cleaned


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_id: uuid.UUID
    origin: PlanEndpoint
    destination: PlanEndpoint
    route_index: int = Field(default=0, ge=0, le=MAX_ROUTE_INDEX)
    # Stored, not used in v1 (no traffic or weather).
    departure_time: AwareDatetime | None = None
    preferences: PlanPreferences | None = None
    # Always wins over telemetry when given (phase4_plan.md D7).
    starting_soc_override: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _endpoints_apart(self) -> Self:
        _check_endpoints_apart(self.origin, self.destination)
        return self


# ---------------------------------------------------------------------------
# §3.7 POST /routes/plan: response
# ---------------------------------------------------------------------------


class PlanEndpointOut(BaseModel):
    lat: float
    lon: float
    label: str | None


class PlanRouteOut(BaseModel):
    route_index: int
    geometry_polyline6: str
    distance_km: float
    drive_minutes: int
    summary: str | None
    countries: list[str]


class PlanStationOut(BaseModel):
    id: uuid.UUID
    name: str
    network: str | None
    # The connector type the plan uses, and its known power (D12).
    connector: str
    power_kw: float
    location: Coordinates


class PlanStopOut(BaseModel):
    sequence: int
    station: PlanStationOut
    distance_along_route_km: float
    distance_from_route_km: float
    arrival_soc: float
    departure_soc: float
    charging_minutes: int
    detour_minutes: int
    # Detours are always estimates (D4): no extra Mapbox call per station.
    detour_is_estimate: Literal[True] = True


class PlanTotals(BaseModel):
    drive_minutes: int
    charging_minutes: int
    detour_minutes: int
    stop_overhead_minutes: int
    # Never invented: Chargewise has no availability data (architecture §6.3).
    waiting_minutes: int = 0
    total_minutes: int
    energy_kwh_estimated: float


SpecSource = Literal["vehicle", "catalog"]


class PlanAssumptions(BaseModel):
    usable_capacity_kwh: float
    usable_capacity_source: SpecSource
    baseline_kwh_per_km: float
    baseline_source: SpecSource
    charging_curve_source: Literal["vehicle", "catalog", "catalog_derived"]
    adjustments_applied: list[str] = Field(default_factory=list)
    reserve_soc: float
    charge_to_soc: float
    max_detour_minutes: int
    preferred_networks: list[str]
    preferred_networks_relaxed: bool
    stop_overhead_minutes: float
    detour_road_factor: float
    detour_speed_kmh: float
    soc_grid_step: float


class PlanCandidates(BaseModel):
    # False when no charging was needed (or none could help): no stations call.
    searched: bool
    corridor_km: float
    # Usable stations handed to the optimizer (after the exclusions below).
    considered: int
    excluded_incompatible: int
    excluded_unknown_power: int
    excluded_detour: int


class PlanLimitingSegment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    from_point: Coordinates = Field(alias="from")
    to_point: Coordinates = Field(alias="to")
    from_km: float
    to_km: float
    required_soc: float
    available_soc: float


class PlanReason(BaseModel):
    code: InfeasibleCode
    message: str
    limiting_segment: PlanLimitingSegment | None


class PlanResponse(BaseModel):
    trip_id: uuid.UUID
    created_at: datetime
    status: Literal["feasible", "infeasible"]
    model_version: str
    vehicle_id: uuid.UUID
    origin: PlanEndpointOut
    destination: PlanEndpointOut
    departure_time: datetime | None
    starting_soc: float
    starting_soc_source: Literal["telemetry", "override"]
    starting_soc_recorded_at: datetime | None
    # Null only for NO_DRIVING_ROUTE.
    route: PlanRouteOut | None
    stops: list[PlanStopOut]
    arrival_soc: float | None
    totals: PlanTotals | None
    assumptions: PlanAssumptions
    candidates: PlanCandidates
    # Reserved for several plans; always empty in v1 (D5).
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    reason: PlanReason | None


__all__ = [
    "DirectionsRequest",
    "DirectionsResponse",
    "DirectionsRouteOut",
    "PlanAssumptions",
    "PlanCandidates",
    "PlanEndpoint",
    "PlanEndpointOut",
    "PlanLimitingSegment",
    "PlanPreferences",
    "PlanReason",
    "PlanRequest",
    "PlanResponse",
    "PlanRouteOut",
    "PlanStationOut",
    "PlanStopOut",
    "PlanTotals",
]
