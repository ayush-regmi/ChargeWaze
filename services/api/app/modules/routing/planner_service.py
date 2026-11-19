"""``POST /routes/plan``: compose route, stations, energy model and optimizer (Phase 4 F4).

``docs/phase4_plan.md`` §3 F4 and §4.5; contract ``docs/api-contracts.md`` §3.7.
Every other module is reached through its service functions, never its tables:

1. Vehicle + specs (``vehicles.garage``), resolved row-then-catalog (D14).
2. Preferences (``users.service``), overlaid with the request's.
3. Starting SoC: the override, else a fresh cached ``soc_percent`` (D7).
4. Directions (Mapbox adapter), on the requested route (D13).
5. If no charging is needed, stop here: no stations call.
6. Stations along the route (``stations.service.search_along_route``), filtered
   to usable candidates (D4, D12) with the exclusions counted.
7. ``optimizer.optimize``, first with preferred networks only.
8. Build the §3.7 body and store it (``trips.service.create_planned_trip``).

The caller commits. Errors raised before step 8 store nothing.

Logging rule (phase3_plan.md D13): counts and stage durations only, never
coordinates, geometry or station names.
"""

from __future__ import annotations

import bisect
import itertools
import logging
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, get_args

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.geo import LatLon, decode_polyline, haversine_km, simplify
from app.integrations.mapbox.client import MapboxClient
from app.integrations.mapbox.schemas import DirectionsRoute, NoRouteReason
from app.integrations.stations.base import ConnectorType, StationFilters, StationProvider
from app.modules.energy import CurvePoint, EnergyVehicle, max_charge_soc
from app.modules.routing.optimizer import optimize
from app.modules.routing.plan_types import (
    CandidateStation,
    FeasiblePlan,
    InfeasiblePlan,
    LimitingSegment,
    PlannerSettings,
    PlanResult,
    RouteProfile,
)
from app.modules.routing.schemas import (
    PlanAssumptions,
    PlanCandidates,
    PlanEndpoint,
    PlanEndpointOut,
    PlanLimitingSegment,
    PlanReason,
    PlanRequest,
    PlanResponse,
    PlanRouteOut,
    PlanStationOut,
    PlanStopOut,
    PlanTotals,
)
from app.modules.stations.schemas import MAX_ALONG_ROUTE_RESULTS, RouteStationOut
from app.modules.stations.service import ROUTE_TOLERANCE_KM, search_along_route
from app.modules.trips.service import NewPlannedStop, TripEndpoint, create_planned_trip
from app.modules.users.service import get_or_create_profile
from app.modules.vehicles.garage import get_vehicle_detail
from app.modules.vehicles.schemas import VehicleDetail
from app.modules.vehicles.spec_catalog import CatalogSpec, lookup_spec
from app.modules.vehicles.vehicle_state import get_vehicle_state
from app.schemas.geo import Coordinates

logger = logging.getLogger(__name__)

MODEL_VERSION = "deterministic-v1"
MIN_CORRIDOR_KM = 0.5
_KNOWN_CONNECTORS: frozenset[str] = frozenset(get_args(ConnectorType))

SpecField = Literal["usable_capacity_kwh", "connectors", "charging_curve", "baseline_kwh_per_km"]

_NO_ROUTE_MESSAGES: dict[NoRouteReason, str] = {
    "no_route": "Mapbox found no drivable route between these points.",
    "no_road_near_point": "The start or destination isn't near a road Mapbox can route on.",
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def plan_trip(
    db: Session,
    *,
    user_id: uuid.UUID,
    request: PlanRequest,
    settings: Settings,
    mapbox: MapboxClient,
    station_provider: StationProvider,
    now: datetime | None = None,
) -> PlanResponse:
    """Plan, store (unsaved) and return one trip. Flushes; the caller commits."""

    current = now or datetime.now(UTC)
    timer = _StageTimer()
    stale_after = timedelta(minutes=settings.telemetry_stale_after_minutes)

    # 1. Vehicle + specs (404 if not the caller's).
    detail = get_vehicle_detail(
        db, user_id=user_id, vehicle_id=request.vehicle_id, stale_after=stale_after, now=current
    )
    specs = resolve_specs(detail)

    # 2. Preferences.
    prefs = _effective_preferences(db, user_id=user_id, request=request, vehicle=specs.vehicle)
    planner_settings = PlannerSettings(
        reserve_soc=prefs.reserve_soc,
        charge_to_soc=prefs.charge_to_soc,
        stop_overhead_minutes=settings.planner_stop_overhead_minutes,
        detour_road_factor=settings.planner_detour_road_factor,
        detour_speed_kmh=settings.planner_detour_speed_kmh,
        soc_grid_step=settings.planner_soc_grid_step,
    )

    # 3. Starting SoC (D7).
    state = get_vehicle_state(
        db, user_id=user_id, vehicle_id=request.vehicle_id, stale_after=stale_after, now=current
    )
    start = _starting_soc(
        request,
        state.signals.soc_percent,
        state.signal_recorded_at.get("soc_percent"),
        stale_after=stale_after,
        now=current,
    )

    corridor_km = corridor_for(prefs.max_detour_minutes, settings)
    assumptions = PlanAssumptions(
        usable_capacity_kwh=specs.vehicle.usable_capacity_kwh,
        usable_capacity_source=specs.capacity_source,
        baseline_kwh_per_km=specs.vehicle.baseline_kwh_per_km,
        baseline_source=specs.baseline_source,
        charging_curve_source=specs.curve_source,
        adjustments_applied=[],
        reserve_soc=prefs.reserve_soc,
        charge_to_soc=prefs.charge_to_soc,
        max_detour_minutes=prefs.max_detour_minutes,
        preferred_networks=list(prefs.preferred_networks),
        preferred_networks_relaxed=False,
        stop_overhead_minutes=settings.planner_stop_overhead_minutes,
        detour_road_factor=settings.planner_detour_road_factor,
        detour_speed_kmh=settings.planner_detour_speed_kmh,
        soc_grid_step=settings.planner_soc_grid_step,
    )
    candidates_out = PlanCandidates(
        searched=False,
        corridor_km=round(corridor_km, 3),
        considered=0,
        excluded_incompatible=0,
        excluded_unknown_power=0,
        excluded_detour=0,
    )
    draft = _Draft(
        trip_id=uuid.uuid4(),
        created_at=current,
        user_id=user_id,
        request=request,
        start=start,
        assumptions=assumptions,
        candidates=candidates_out,
        is_simulated=state.source == "simulated",
    )

    # 4. Directions (D13): the server's own route, never client geometry.
    with timer("directions"):
        directions = mapbox.directions(request.origin.as_tuple(), request.destination.as_tuple())
    if not directions.routes:
        reason = directions.no_route_reason or "no_route"
        result: PlanResult = InfeasiblePlan(
            code="NO_DRIVING_ROUTE", message=_NO_ROUTE_MESSAGES[reason], limiting_segment=None
        )
        return _finish(db, draft, route=None, result=result, timer=timer)
    if request.route_index >= len(directions.routes):
        raise ApiError(
            "VALIDATION_ERROR",
            "route_index is beyond the routes Mapbox returned.",
            status_code=422,
            details={"reason": "ROUTE_INDEX_UNAVAILABLE", "route_count": len(directions.routes)},
        )
    route = _RouteGeometry.from_directions(
        directions.routes[request.route_index],
        route_index=request.route_index,
        max_points=settings.stations_route_max_points,
    )

    # 5. No charging needed (or none can help): skip the stations call.
    with timer("optimizer"):
        result = optimize(route.profile, [], specs.vehicle, planner_settings, start.soc)
    if isinstance(result, FeasiblePlan) or result.code == "STARTING_SOC_TOO_LOW":
        return _finish(db, draft, route=route, result=result, timer=timer)

    # 6. Stations along the route, filtered to usable candidates.
    with timer("stations"):
        found = search_along_route(
            db,
            station_provider,
            route=route.points,
            corridor_km=corridor_km,
            filters=StationFilters(
                connector_types=tuple(sorted(specs.connectors)), charging_level="dc_fast"
            ),
            vehicle_connectors=specs.connectors,
            max_results=MAX_ALONG_ROUTE_RESULTS,
            route_max_points=settings.stations_route_max_points,
            now=current,
        )
    selection = select_candidates(
        found.items,
        vehicle_connectors=specs.connectors,
        max_detour_minutes=prefs.max_detour_minutes,
        settings=planner_settings,
        along_scale=route.along_scale,
        route_km=route.profile.distance_km,
    )
    draft.candidates = candidates_out.model_copy(
        update={
            "searched": True,
            "considered": len(selection.candidates),
            "excluded_incompatible": selection.excluded_incompatible,
            "excluded_unknown_power": selection.excluded_unknown_power,
            "excluded_detour": selection.excluded_detour,
        }
    )

    # 7. Optimize, preferred networks first.
    with timer("optimizer"):
        result, relaxed = _optimize_with_preferences(
            route.profile,
            selection,
            specs.vehicle,
            planner_settings,
            start.soc,
            prefs.preferred_networks,
        )
    draft.assumptions = assumptions.model_copy(update={"preferred_networks_relaxed": relaxed})
    return _finish(db, draft, route=route, result=result, timer=timer)


# ---------------------------------------------------------------------------
# Step 1: vehicle specs (D14)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSpecs:
    vehicle: EnergyVehicle
    connectors: frozenset[ConnectorType]
    capacity_source: Literal["vehicle", "catalog"]
    baseline_source: Literal["vehicle", "catalog"]
    curve_source: Literal["vehicle", "catalog", "catalog_derived"]


def resolve_specs(detail: VehicleDetail) -> ResolvedSpecs:
    """Row value if set, else the catalog value, else missing (D14).

    A row value equal to the catalog's is reported as ``catalog``: connecting a
    catalog vehicle copies the catalog into its row (P4-F2), and the UI must not
    call derived figures the user's own. Missing → 422 ``VEHICLE_SPECS_INCOMPLETE``.
    """

    catalog = lookup_spec(detail.make, detail.model, detail.year)
    row = detail.specs
    missing: list[SpecField] = []

    # Capacity.
    catalog_capacity = _catalog_capacity(catalog)
    row_capacity = row.usable_capacity_kwh if row is not None else None
    capacity = row_capacity if row_capacity is not None else catalog_capacity
    capacity_source: Literal["vehicle", "catalog"] = (
        "vehicle" if row_capacity is not None and row_capacity != catalog_capacity else "catalog"
    )
    if capacity is None or capacity <= 0:
        missing.append("usable_capacity_kwh")

    # Connectors (unknown strings are ignored).
    row_connectors = _known_connectors(row.connectors if row is not None else [])
    connectors = row_connectors or _known_connectors(catalog.connectors if catalog else ())
    if not connectors:
        missing.append("connectors")

    # Charging curve.
    catalog_curve = _curve(catalog.charging_curve) if catalog else ()
    row_curve = (
        tuple(CurvePoint(soc=p.soc, power_kw=p.power_kw) for p in row.charging_curve)
        if row is not None
        else ()
    )
    curve = row_curve or catalog_curve
    if row_curve and row_curve != catalog_curve:
        curve_source: Literal["vehicle", "catalog", "catalog_derived"] = "vehicle"
    elif catalog is not None and catalog.charging_curve_source == "derived":
        curve_source = "catalog_derived"
    else:
        curve_source = "catalog"
    if not curve:
        missing.append("charging_curve")

    # Baseline (P4-F8). Connecting never copies the catalog baseline into the
    # row, so a row value is always the user's own and reported as ``vehicle``.
    row_baseline = (
        float(row.baseline_kwh_per_km)
        if row is not None and row.baseline_kwh_per_km is not None
        else None
    )
    catalog_baseline = (
        float(catalog.baseline_kwh_per_km)
        if catalog is not None and catalog.baseline_kwh_per_km is not None
        else None
    )
    baseline = row_baseline if row_baseline is not None else catalog_baseline
    baseline_source: Literal["vehicle", "catalog"] = (
        "vehicle" if row_baseline is not None else "catalog"
    )
    if baseline is None or baseline <= 0:
        missing.append("baseline_kwh_per_km")

    if not missing:
        assert capacity is not None and baseline is not None
        try:
            vehicle = EnergyVehicle(
                usable_capacity_kwh=capacity, baseline_kwh_per_km=baseline, charging_curve=curve
            )
        except ValueError:
            # Capacity and baseline were checked above, so a user-entered curve
            # the model can't use (unsorted, < 2 points, zero power) is the cause.
            missing.append("charging_curve")
        else:
            return ResolvedSpecs(
                vehicle=vehicle,
                connectors=connectors,
                capacity_source=capacity_source,
                baseline_source=baseline_source,
                curve_source=curve_source,
            )
    raise ApiError(
        "VALIDATION_ERROR",
        "The vehicle is missing specs the planner needs.",
        status_code=422,
        details={"reason": "VEHICLE_SPECS_INCOMPLETE", "missing": missing},
    )


def _catalog_capacity(catalog: CatalogSpec | None) -> float | None:
    if catalog is None or catalog.usable_capacity_kwh is None:
        return None
    return float(catalog.usable_capacity_kwh)


def _known_connectors(values: Sequence[str]) -> frozenset[ConnectorType]:
    return frozenset(v for v in values if v in _KNOWN_CONNECTORS)  # type: ignore[misc]


def _curve(points: Sequence[dict[str, float]]) -> tuple[CurvePoint, ...]:
    return tuple(CurvePoint(soc=float(p["soc"]), power_kw=float(p["power_kw"])) for p in points)


# ---------------------------------------------------------------------------
# Step 2: preferences
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectivePreferences:
    reserve_soc: float
    charge_to_soc: float  # min(preferred_charge_to, vehicle max_charge_soc)
    max_detour_minutes: int
    preferred_networks: tuple[str, ...]


def _effective_preferences(
    db: Session, *, user_id: uuid.UUID, request: PlanRequest, vehicle: EnergyVehicle
) -> EffectivePreferences:
    """Request keys win over stored preferences, which win over the defaults
    (seeded with the profile). Nothing is written back."""

    profile = get_or_create_profile(db, user_id=user_id)
    # A just-created profile's ``preferences`` relationship isn't loaded yet.
    db.refresh(profile, ["preferences"])
    stored = profile.preferences
    overrides = request.preferences.model_dump(exclude_unset=True) if request.preferences else {}

    def pick(key: str, default: Any) -> Any:
        if key in overrides:
            return overrides[key]
        value = getattr(stored, key, None) if stored is not None else None
        return default if value is None else value

    reserve = Decimal(str(pick("reserve_soc", Decimal("0.15"))))
    charge_to = Decimal(str(pick("preferred_charge_to", Decimal("0.80"))))
    if reserve >= charge_to:
        raise ApiError(
            "VALIDATION_ERROR",
            "reserve_soc must be lower than preferred_charge_to.",
            status_code=422,
            details={"fields": ["reserve_soc", "preferred_charge_to"]},
        )
    effective_charge_to = min(float(charge_to), max_charge_soc(vehicle))
    if float(reserve) >= effective_charge_to:
        raise ApiError(
            "VALIDATION_ERROR",
            "reserve_soc must be lower than the vehicle's highest chargeable SoC.",
            status_code=422,
            details={"fields": ["reserve_soc"], "max_charge_soc": effective_charge_to},
        )
    return EffectivePreferences(
        reserve_soc=float(reserve),
        charge_to_soc=effective_charge_to,
        max_detour_minutes=int(pick("max_detour_minutes", 20)),
        preferred_networks=tuple(pick("preferred_networks", [])),
    )


# ---------------------------------------------------------------------------
# Step 3: starting SoC (D7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StartingSoc:
    soc: float
    source: Literal["telemetry", "override"]
    recorded_at: datetime | None


def _starting_soc(
    request: PlanRequest,
    soc_percent: float | None,
    recorded_at: datetime | None,
    *,
    stale_after: timedelta,
    now: datetime,
) -> StartingSoc:
    if request.starting_soc_override is not None:
        return StartingSoc(soc=request.starting_soc_override, source="override", recorded_at=None)

    last_soc = soc_percent / 100 if soc_percent is not None and 0 <= soc_percent <= 100 else None
    fresh = recorded_at is not None and now - recorded_at <= stale_after
    if last_soc is not None and fresh:
        return StartingSoc(soc=last_soc, source="telemetry", recorded_at=recorded_at)
    raise ApiError(
        "VALIDATION_ERROR",
        "The vehicle's charge reading is missing or out of date. Enter a starting charge.",
        status_code=422,
        details={
            "reason": "STARTING_SOC_REQUIRED",
            "last_soc": None if last_soc is None else round(last_soc, 3),
            "last_recorded_at": (
                None
                if last_soc is None or recorded_at is None
                else recorded_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            ),
        },
    )


# ---------------------------------------------------------------------------
# Step 4: route geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RouteGeometry:
    """The chosen Mapbox route and the line stations are measured on.

    P3-F5 measures ``distance_along_route_km`` on the *simplified* polyline,
    while the optimizer works in Mapbox's road distance. ``along_scale`` maps
    the first onto the second so stations and the route share one km axis.
    """

    route: DirectionsRoute
    route_index: int
    points: list[LatLon]
    simplified: list[LatLon]
    cumulative_km: list[float]  # along ``simplified``
    profile: RouteProfile

    @classmethod
    def from_directions(
        cls, route: DirectionsRoute, *, route_index: int, max_points: int
    ) -> _RouteGeometry:
        points = decode_polyline(route.geometry_polyline6)
        simplified = (
            simplify(points, tolerance_km=ROUTE_TOLERANCE_KM, max_points=max_points)
            if len(points) >= 2
            else points
        )
        cumulative = [0.0]
        for a, b in itertools.pairwise(simplified):
            cumulative.append(cumulative[-1] + haversine_km(a, b))
        return cls(
            route=route,
            route_index=route_index,
            points=points,
            simplified=simplified,
            cumulative_km=cumulative,
            profile=RouteProfile(
                distance_km=route.distance_m / 1000.0, duration_minutes=route.duration_s / 60.0
            ),
        )

    @property
    def along_scale(self) -> float:
        line_km = self.cumulative_km[-1]
        return self.profile.distance_km / line_km if line_km > 0 else 1.0

    def point_at(self, road_km: float) -> Coordinates:
        """The point ``road_km`` along the route, interpolated on the simplified line."""

        target = max(0.0, road_km) / self.along_scale
        cumulative = self.cumulative_km
        if len(self.simplified) == 1 or target >= cumulative[-1]:
            lat, lon = self.simplified[-1]
            return Coordinates(lat=lat, lon=lon)
        i = max(1, bisect.bisect_right(cumulative, target))
        seg = cumulative[i] - cumulative[i - 1]
        t = 0.0 if seg == 0 else (target - cumulative[i - 1]) / seg
        (lat1, lon1), (lat2, lon2) = self.simplified[i - 1], self.simplified[i]
        return Coordinates(lat=lat1 + t * (lat2 - lat1), lon=lon1 + t * (lon2 - lon1))


def corridor_for(max_detour_minutes: int, settings: Settings) -> float:
    """Widest off-route distance whose estimated detour fits ``max_detour_minutes`` (D4)."""

    km = (
        max_detour_minutes
        * settings.planner_detour_speed_kmh
        / 60
        / (2 * settings.planner_detour_road_factor)
    )
    return min(max(km, MIN_CORRIDOR_KM), settings.planner_max_corridor_km)


# ---------------------------------------------------------------------------
# Step 6: candidates (D4, D12)
# ---------------------------------------------------------------------------


@dataclass
class CandidateSelection:
    candidates: list[CandidateStation] = field(default_factory=list)
    # Station id → the cached station, for network matching and the response.
    stations: dict[uuid.UUID, RouteStationOut] = field(default_factory=dict)
    excluded_incompatible: int = 0
    excluded_unknown_power: int = 0
    excluded_detour: int = 0


def select_candidates(
    stations: Sequence[RouteStationOut],
    *,
    vehicle_connectors: frozenset[ConnectorType],
    max_detour_minutes: int,
    settings: PlannerSettings,
    along_scale: float,
    route_km: float,
) -> CandidateSelection:
    """Usable stations, in the order the stations module returned them.

    A station is usable when a compatible connector has a known power (D12; the
    highest such power and its connector are used) and its estimated detour
    fits ``max_detour_minutes`` (D4). Exclusions are counted in that order.
    """

    selection = CandidateSelection()
    for s in stations:
        compatible = [c for c in s.connectors if c.type in vehicle_connectors]
        if not compatible:
            selection.excluded_incompatible += 1
            continue
        known = [c for c in compatible if c.power_kw is not None and c.power_kw > 0]
        if not known:
            selection.excluded_unknown_power += 1
            continue
        detour_km = 2 * s.distance_from_route_km * settings.detour_road_factor
        detour_minutes = detour_km / settings.detour_speed_kmh * 60
        if detour_minutes > max_detour_minutes:
            selection.excluded_detour += 1
            continue
        best = max(known, key=lambda c: (c.power_kw or 0.0, c.type))
        assert best.power_kw is not None
        selection.candidates.append(
            CandidateStation(
                station_id=s.id,
                name=s.name,
                network=s.network,
                connector=best.type,
                power_kw=best.power_kw,
                distance_along_route_km=min(
                    max(s.distance_along_route_km * along_scale, 0.0), route_km
                ),
                distance_from_route_km=s.distance_from_route_km,
                lat=s.location.lat,
                lon=s.location.lon,
            )
        )
        selection.stations[s.id] = s
    return selection


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def matches_preferred(station: RouteStationOut, preferred: Sequence[str]) -> bool:
    """Fuzzy match (phase4_plan.md F4): the station's network or operator,
    normalized, contains a preferred name or is contained in one."""

    wanted = [_norm(p) for p in preferred if p.strip()]
    for value in (station.network, station.operator):
        if not value:
            continue
        have = _norm(value)
        if any(w in have or have in w for w in wanted):
            return True
    return False


def _optimize_with_preferences(
    route: RouteProfile,
    selection: CandidateSelection,
    vehicle: EnergyVehicle,
    settings: PlannerSettings,
    starting_soc: float,
    preferred: Sequence[str],
) -> tuple[PlanResult, bool]:
    """Plan with preferred networks only; if that's infeasible, with every candidate.

    Returns the result and whether the preference had to be relaxed.
    """

    if preferred:
        subset = [
            c
            for c in selection.candidates
            if matches_preferred(selection.stations[c.station_id], preferred)
        ]
        if len(subset) < len(selection.candidates):
            first = optimize(route, subset, vehicle, settings, starting_soc)
            if isinstance(first, FeasiblePlan):
                return first, False
            return optimize(route, selection.candidates, vehicle, settings, starting_soc), True
    return optimize(route, selection.candidates, vehicle, settings, starting_soc), False


# ---------------------------------------------------------------------------
# Step 8: response + persistence
# ---------------------------------------------------------------------------


@dataclass
class _Draft:
    trip_id: uuid.UUID
    created_at: datetime
    user_id: uuid.UUID
    request: PlanRequest
    start: StartingSoc
    assumptions: PlanAssumptions
    candidates: PlanCandidates
    is_simulated: bool


def _finish(
    db: Session,
    draft: _Draft,
    *,
    route: _RouteGeometry | None,
    result: PlanResult,
    timer: _StageTimer,
) -> PlanResponse:
    response = build_response(draft, route=route, result=result)
    with timer("persist"):
        create_planned_trip(
            db,
            trip_id=draft.trip_id,
            created_at=draft.created_at,
            user_id=draft.user_id,
            vehicle_id=draft.request.vehicle_id,
            origin=_trip_endpoint(draft.request.origin),
            destination=_trip_endpoint(draft.request.destination),
            departure_time=draft.request.departure_time,
            plan_status=response.status,
            plan_payload=response.model_dump(mode="json", by_alias=True),
            model_version=MODEL_VERSION,
            stops=[
                NewPlannedStop(
                    sequence=s.sequence,
                    station_id=s.station.id,
                    arrival_soc=s.arrival_soc,
                    departure_soc=s.departure_soc,
                    charging_minutes=s.charging_minutes,
                )
                for s in response.stops
            ],
            is_simulated=draft.is_simulated,
        )
    logger.info(
        "route_plan",
        extra={
            "plan_status": response.status,
            "reason": response.reason.code if response.reason else None,
            "stops": len(response.stops),
            "searched": response.candidates.searched,
            "considered": response.candidates.considered,
            "excluded_incompatible": response.candidates.excluded_incompatible,
            "excluded_unknown_power": response.candidates.excluded_unknown_power,
            "excluded_detour": response.candidates.excluded_detour,
            "preferred_networks_relaxed": response.assumptions.preferred_networks_relaxed,
            **timer.as_log_fields(),
        },
    )
    return response


def build_response(
    draft: _Draft, *, route: _RouteGeometry | None, result: PlanResult
) -> PlanResponse:
    request = draft.request
    route_out = (
        None
        if route is None
        else PlanRouteOut(
            route_index=route.route_index,
            geometry_polyline6=route.route.geometry_polyline6,
            distance_km=round(route.profile.distance_km, 3),
            drive_minutes=round_minutes(route.profile.duration_minutes),
            summary=route.route.summary,
            countries=list(route.route.countries),
        )
    )
    common: dict[str, Any] = {
        "trip_id": draft.trip_id,
        "created_at": draft.created_at,
        "model_version": MODEL_VERSION,
        "vehicle_id": request.vehicle_id,
        "origin": _endpoint_out(request.origin),
        "destination": _endpoint_out(request.destination),
        "departure_time": request.departure_time,
        "starting_soc": round_soc(draft.start.soc),
        "starting_soc_source": draft.start.source,
        "starting_soc_recorded_at": draft.start.recorded_at,
        "route": route_out,
        "assumptions": draft.assumptions,
        "candidates": draft.candidates,
        "alternatives": [],
    }

    if isinstance(result, InfeasiblePlan):
        return PlanResponse(
            **common,
            status="infeasible",
            stops=[],
            arrival_soc=None,
            totals=None,
            reason=PlanReason(
                code=result.code,
                message=result.message,
                limiting_segment=(
                    None
                    if result.limiting_segment is None or route is None
                    else _segment_out(result.limiting_segment, route)
                ),
            ),
        )

    stops = [
        PlanStopOut(
            sequence=i,
            station=PlanStationOut(
                id=stop.station.station_id,
                name=stop.station.name,
                network=stop.station.network,
                connector=stop.station.connector,
                power_kw=stop.station.power_kw,
                location=Coordinates(lat=stop.station.lat, lon=stop.station.lon),
            ),
            distance_along_route_km=round(stop.station.distance_along_route_km, 3),
            distance_from_route_km=round(stop.station.distance_from_route_km, 3),
            arrival_soc=round_soc(stop.arrival_soc),
            departure_soc=round_soc(stop.departure_soc),
            charging_minutes=round_minutes(stop.charging_minutes),
            detour_minutes=round_minutes(stop.detour_minutes),
        )
        for i, stop in enumerate(result.stops, start=1)
    ]
    return PlanResponse(
        **common,
        status="feasible",
        stops=stops,
        arrival_soc=round_soc(result.arrival_soc),
        totals=PlanTotals(
            drive_minutes=round_minutes(result.drive_minutes),
            charging_minutes=round_minutes(result.charging_minutes),
            detour_minutes=round_minutes(result.detour_minutes),
            stop_overhead_minutes=round_minutes(result.overhead_minutes),
            waiting_minutes=0,
            total_minutes=round_minutes(result.total_minutes),
            energy_kwh_estimated=round(result.energy_kwh, 2),
        ),
        reason=None,
    )


def _segment_out(segment: LimitingSegment, route: _RouteGeometry) -> PlanLimitingSegment:
    return PlanLimitingSegment.model_validate(
        {
            "from": route.point_at(segment.from_km),
            "to": route.point_at(segment.to_km),
            "from_km": round(segment.from_km, 3),
            "to_km": round(segment.to_km, 3),
            "required_soc": round_soc(segment.required_soc),
            "available_soc": round_soc(segment.available_soc),
        }
    )


def _endpoint_out(endpoint: PlanEndpoint) -> PlanEndpointOut:
    return PlanEndpointOut(lat=endpoint.lat, lon=endpoint.lon, label=endpoint.label)


def _trip_endpoint(endpoint: PlanEndpoint) -> TripEndpoint:
    return TripEndpoint(lat=endpoint.lat, lon=endpoint.lon, label=endpoint.label)


def round_soc(value: float) -> float:
    """SoC fraction to 3 decimals, half-up (D16)."""

    return float(Decimal(repr(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def round_minutes(value: float) -> int:
    """Whole minutes, half-up (D16)."""

    return int(Decimal(repr(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


class _StageTimer:
    """Wall-clock milliseconds per stage, summed when a stage runs twice."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self.ms: dict[str, float] = {}

    @contextmanager
    def __call__(self, stage: str) -> Iterator[None]:
        started = self._clock()
        try:
            yield
        finally:
            elapsed = (self._clock() - started) * 1000
            self.ms[stage] = self.ms.get(stage, 0.0) + elapsed

    def as_log_fields(self) -> dict[str, float]:
        return {f"{stage}_ms": round(ms, 1) for stage, ms in self.ms.items()}


__all__ = [
    "MODEL_VERSION",
    "CandidateSelection",
    "ResolvedSpecs",
    "build_response",
    "corridor_for",
    "matches_preferred",
    "plan_trip",
    "resolve_specs",
    "round_minutes",
    "round_soc",
    "select_candidates",
]
