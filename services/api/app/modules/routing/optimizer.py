"""Charging-stop optimizer: route-ordered dynamic programming (ADR 0008).

``docs/phase4_plan.md`` §4.3. Pure: no database, no HTTP. The input is already
filtered by F4 (compatible connector, known power, within the detour limit), so
every candidate here is usable.

State: (station, departure SoC on the ``soc_grid_step`` grid). Value: the least
``charging + detour + overhead`` minutes to leave that station at that SoC.
Drive time is the same for every plan on one route (D13), so it is added once at
the end. Stations are visited in route order, so the graph has no cycles.

For station *j* the cost of departing at grid SoC ``d`` is::

    min over predecessors p with arrival a_p < d of
        (cost_p - F_j(a_p)) + F_j(d) + detour_j + overhead

where ``F_j`` is the cumulative charge-time function at *j*'s power. Sorting the
predecessors by ``a_p`` and keeping a running minimum of ``cost_p - F_j(a_p)``
makes every grid state O(1) after the sort.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.modules.energy import charging_minutes, cumulative_charge_minutes, max_charge_soc
from app.modules.energy.model import segment_energy_kwh, soc_used
from app.modules.energy.types import EnergyVehicle
from app.modules.routing.plan_types import (
    CandidateStation,
    FeasiblePlan,
    InfeasiblePlan,
    LimitingSegment,
    PlannedStop,
    PlannerSettings,
    PlanResult,
    RouteProfile,
)

# Float noise allowed when an arrival SoC is compared with the reserve, so a
# plan that lands exactly on the reserve isn't rejected by rounding.
_EPS = 1e-9
# Decimals kept when two plan costs are compared, so float noise doesn't decide
# between plans that tie on time (the tie-breaks then apply, §4.3 step 5).
_COST_DECIMALS = 9

# Sort key of a label: (cost, stops, charging minutes, station indices in route order).
_Key = tuple[float, int, float, tuple[int, ...]]


@dataclass(slots=True)
class _Label:
    """Best way found to leave station ``station`` at grid SoC ``departure``."""

    cost: float  # charging + detour + overhead minutes so far
    charging: float  # charging minutes so far
    stops: int
    path: tuple[int, ...]  # station indices (route order) used so far, ending with this one
    arrival: float  # SoC on arrival at this station
    departure: float
    prev: _Label | None  # the origin's label has no path and no prev


@dataclass(frozen=True, slots=True)
class _Node:
    station: CandidateStation
    along_km: float
    detour_km: float  # 2 · off-route · road factor, charged to the leg arriving here
    detour_minutes: float
    minutes_from_empty: Callable[[float], float]


class _Problem:
    """Everything derived from the inputs once, shared by the searches."""

    def __init__(
        self,
        route: RouteProfile,
        candidates: Sequence[CandidateStation],
        vehicle: EnergyVehicle,
        settings: PlannerSettings,
        starting_soc: float,
    ) -> None:
        self.route = route
        self.vehicle = vehicle
        self.settings = settings
        self.starting_soc = starting_soc
        self.soc_per_km = soc_used(vehicle, 1.0)

        step = settings.soc_grid_step
        top = math.floor(settings.charge_to_soc / step + _EPS)
        self.grid = [round(k * step, 12) for k in range(top + 1)]
        self.grid_max = self.grid[-1]

        curves: dict[float, Callable[[float], float]] = {}
        nodes = []
        for station in sorted(candidates, key=lambda c: (c.distance_along_route_km, c.station_id)):
            curve = curves.get(station.power_kw)
            if curve is None:
                curve = cumulative_charge_minutes(vehicle, station.power_kw)
                curves[station.power_kw] = curve
            detour_km = 2.0 * station.distance_from_route_km * settings.detour_road_factor
            nodes.append(
                _Node(
                    station=station,
                    along_km=station.distance_along_route_km,
                    detour_km=detour_km,
                    detour_minutes=detour_km / settings.detour_speed_kmh * 60.0,
                    minutes_from_empty=curve,
                )
            )
        self.nodes: list[_Node] = nodes

    def leg_soc(self, from_km: float, node: _Node) -> float:
        """SoC used from ``from_km`` on the route to ``node`` (incl. its detour)."""

        return (node.along_km - from_km + node.detour_km) * self.soc_per_km

    def final_leg_km(self, from_km: float) -> float:
        return max(0.0, self.route.distance_km - from_km)

    # ------------------------------------------------------------------ search

    def search(self, reserve: float) -> list[_Label] | None:
        """Minimum-time plan with ``reserve`` at every arrival, as the labels of
        its stops in order (empty: no stop needed), or ``None`` if there is none.

        Every label is placed on one axis, its *potential*
        ``v = departure + along_km · soc_per_km``: the SoC it would have at km 0.
        Detours are charged to the leg arriving at a station, so the arrival at
        station *j* from any earlier label is ``v - (along_j + detour_j) · soc_per_km``,
        whichever station the label is at. A label is therefore useless if another
        earlier label has at least its potential for no more cost, and the search
        keeps only the Pareto frontier of (potential, cost). That frontier is small
        (the origin alone dominates every label below the starting SoC), which is
        what keeps a 120-station corridor well under the 500 ms target.
        """

        floor = reserve - _EPS
        if self.starting_soc - self.final_leg_km(0.0) * self.soc_per_km >= floor:
            return []

        grid = self.grid
        origin = _Label(
            cost=0.0,
            charging=0.0,
            stops=0,
            path=(),
            arrival=self.starting_soc,
            departure=self.starting_soc,
            prev=None,
        )
        # Pareto frontier, potential ascending (so cost ascending too).
        frontier: list[tuple[float, _Key, _Label]] = [(self.starting_soc, _key(origin), origin)]
        best: tuple[_Key, _Label] | None = None

        for j, node in enumerate(self.nodes):
            F = node.minutes_from_empty
            offset = (node.along_km + node.detour_km) * self.soc_per_km
            potentials = [entry[0] for entry in frontier]
            lo = bisect_left(potentials, floor + offset)
            hi = bisect_left(potentials, self.grid_max + offset)
            if lo >= hi:
                continue  # nothing reaches this station with charging left to do

            fixed = node.detour_minutes + self.settings.stop_overhead_minutes
            run: tuple[_Key, float, float, _Label] | None = None
            new: list[tuple[float, _Key, _Label]] = []
            p = lo
            for departure in grid:
                # Predecessors arrive in potential order, so a running minimum of
                # cost - F(arrival) over arrivals below ``departure`` is exact.
                while p < hi and frontier[p][0] - offset < departure - 1e-12:
                    _, _, prev = frontier[p]
                    arrival = max(frontier[p][0] - offset, 0.0)
                    f_a = F(arrival)
                    key: _Key = (
                        round(prev.cost - f_a, _COST_DECIMALS),
                        prev.stops,
                        round(prev.charging - f_a, _COST_DECIMALS),
                        prev.path,
                    )
                    if run is None or key < run[0]:
                        run = (key, arrival, f_a, prev)
                    p += 1
                if run is None:
                    continue
                _, arrival, f_a, prev = run
                charge = F(departure) - f_a
                label = _Label(
                    cost=prev.cost + charge + fixed,
                    charging=prev.charging + charge,
                    stops=prev.stops + 1,
                    path=(*prev.path, j),
                    arrival=arrival,
                    departure=departure,
                    prev=prev,
                )
                label_key = _key(label)
                new.append((departure + node.along_km * self.soc_per_km, label_key, label))
                reaches_end = (
                    departure - self.final_leg_km(node.along_km) * self.soc_per_km >= floor
                )
                if reaches_end and (best is None or label_key < best[0]):
                    best = (label_key, label)

            frontier = _pareto(frontier + new)

        if best is None:
            return None
        chain: list[_Label] = []
        cursor: _Label | None = best[1]
        while cursor is not None and cursor.path:
            chain.append(cursor)
            cursor = cursor.prev
        chain.reverse()
        return chain

    # ------------------------------------------------------------------ results

    def feasible(self, chain: list[_Label]) -> FeasiblePlan:
        stops = []
        soc = self.starting_soc
        position = 0.0
        detour_km_total = 0.0
        for label in chain:
            node = self.nodes[label.path[-1]]
            arrival = soc - self.leg_soc(position, node)
            minutes = charging_minutes(
                self.vehicle, max(arrival, 0.0), label.departure, node.station.power_kw
            )
            stops.append(
                PlannedStop(
                    station=node.station,
                    arrival_soc=arrival,
                    departure_soc=label.departure,
                    charging_minutes=minutes,
                    detour_km=node.detour_km,
                    detour_minutes=node.detour_minutes,
                )
            )
            detour_km_total += node.detour_km
            soc = label.departure
            position = node.along_km
        arrival_soc = soc - self.final_leg_km(position) * self.soc_per_km

        charging_total = sum(s.charging_minutes for s in stops)
        detour_total = sum(s.detour_minutes for s in stops)
        overhead = len(stops) * self.settings.stop_overhead_minutes
        drive = self.route.duration_minutes
        return FeasiblePlan(
            stops=tuple(stops),
            arrival_soc=arrival_soc,
            drive_minutes=drive,
            charging_minutes=charging_total,
            detour_minutes=detour_total,
            overhead_minutes=overhead,
            total_minutes=drive + charging_total + detour_total + overhead,
            energy_kwh=segment_energy_kwh(self.vehicle, self.route.distance_km + detour_km_total),
        )

    def reserve_unreachable(self, chain: list[_Label]) -> InfeasiblePlan:
        """The reserve-0 plan works but the real one doesn't. Report the leg of the
        reserve-0 plan that misses the reserve by the most even when leaving at the
        most SoC possible (the start SoC, or the top grid SoC at a charger); ties go
        to the lowest arrival."""

        reserve = self.settings.reserve_soc
        # (shortfall, -arrival, from_km, to_km, soc used, best SoC available at from_km)
        legs: list[tuple[float, float, float, float, float, float]] = []
        position, soc, available = 0.0, self.starting_soc, self.starting_soc
        for label in chain:
            node = self.nodes[label.path[-1]]
            used = self.leg_soc(position, node)
            legs.append(
                (reserve + used - available, used - soc, position, node.along_km, used, available)
            )
            position, soc, available = node.along_km, label.departure, self.grid_max
        used = self.final_leg_km(position) * self.soc_per_km
        legs.append(
            (
                reserve + used - available,
                used - soc,
                position,
                self.route.distance_km,
                used,
                available,
            )
        )
        _, _, from_km, to_km, used, available = max(legs)
        return InfeasiblePlan(
            code="RESERVE_SOC_UNREACHABLE",
            message=(
                "This trip only works by arriving below your reserve "
                f"({_pct(reserve)}). Lower the reserve or raise the charge-to limit."
            ),
            limiting_segment=LimitingSegment(
                from_km=from_km,
                to_km=to_km,
                required_soc=reserve + used,
                available_soc=available,
            ),
        )

    def unreachable(self) -> InfeasiblePlan:
        """No plan even with reserve 0. A forward pass with the most SoC possible at
        every charger finds how far the car gets while keeping the reserve."""

        reserve = self.settings.reserve_soc
        floor = reserve - _EPS
        departures: list[float | None] = []
        for node in self.nodes:
            best_arrival = self.starting_soc - self.leg_soc(0.0, node)
            for i, prev_departure in enumerate(departures):
                if prev_departure is not None:
                    arrival = prev_departure - self.leg_soc(self.nodes[i].along_km, node)
                    best_arrival = max(best_arrival, arrival)
            departures.append(max(self.grid_max, best_arrival) if best_arrival >= floor else None)

        reachable = [j for j, d in enumerate(departures) if d is not None]
        if not reachable:
            first = self.nodes[0] if self.nodes else None
            required_km = first.along_km + first.detour_km if first else self.route.distance_km
            return InfeasiblePlan(
                code="NO_COMPATIBLE_CHARGER_IN_RANGE",
                message=(
                    "No compatible charger is within reach from the start "
                    f"while keeping your reserve ({_pct(reserve)})."
                ),
                limiting_segment=LimitingSegment(
                    from_km=0.0,
                    to_km=min(
                        self.route.distance_km,
                        max(0.0, (self.starting_soc - reserve) / self.soc_per_km),
                    ),
                    required_soc=reserve + required_km * self.soc_per_km,
                    available_soc=self.starting_soc,
                ),
            )

        furthest = max(reachable, key=lambda j: (self.nodes[j].along_km, j))
        from_km = self.nodes[furthest].along_km
        available = departures[furthest]
        assert available is not None
        if furthest + 1 < len(self.nodes):
            nxt = self.nodes[furthest + 1]
            to_km = nxt.along_km
            used = self.leg_soc(from_km, nxt)
        else:
            to_km = self.route.distance_km
            used = self.final_leg_km(from_km) * self.soc_per_km
        return InfeasiblePlan(
            code="DESTINATION_UNREACHABLE",
            message=(
                f"The gap between chargers after km {from_km:.0f} is too long to cross, "
                f"even charging to {_pct(self.grid_max)}."
            ),
            limiting_segment=LimitingSegment(
                from_km=from_km,
                to_km=to_km,
                required_soc=reserve + used,
                available_soc=available,
            ),
        )


def optimize(
    route: RouteProfile,
    candidates: Sequence[CandidateStation],
    vehicle: EnergyVehicle,
    settings: PlannerSettings,
    starting_soc: float,
) -> PlanResult:
    """Minimum-time charging plan for one route (ADR 0008, phase4_plan.md §4.3).

    ``candidates`` are already filtered by F4. Raises ``ValueError`` for inputs
    F4 must never send (out-of-range SoCs or settings, non-positive power)."""

    _validate(route, candidates, vehicle, settings, starting_soc)
    if starting_soc <= settings.reserve_soc:
        return InfeasiblePlan(
            code="STARTING_SOC_TOO_LOW",
            message=(
                f"The starting charge ({_pct(starting_soc)}) is at or below your "
                f"reserve ({_pct(settings.reserve_soc)}). Charge before leaving."
            ),
            limiting_segment=None,
        )

    problem = _Problem(route, candidates, vehicle, settings, starting_soc)
    chain = problem.search(settings.reserve_soc)
    if chain is not None:
        return problem.feasible(chain)
    if settings.reserve_soc > 0:
        relaxed = problem.search(0.0)
        if relaxed is not None:
            return problem.reserve_unreachable(relaxed)
    return problem.unreachable()


def _key(label: _Label) -> _Key:
    """§4.3 step 5: least time, then fewer stops, less charging, earlier stations."""

    return (
        round(label.cost, _COST_DECIMALS),
        label.stops,
        round(label.charging, _COST_DECIMALS),
        label.path,
    )


def _pareto(entries: list[tuple[float, _Key, _Label]]) -> list[tuple[float, _Key, _Label]]:
    """Keep each label unless another has at least its potential and a key no
    worse; returned in ascending potential."""

    entries.sort(key=lambda e: (-e[0], e[1]))
    kept: list[tuple[float, _Key, _Label]] = []
    for entry in entries:
        if not kept or entry[1] < kept[-1][1]:
            kept.append(entry)
    kept.reverse()
    return kept


def _pct(soc: float) -> str:
    return f"{soc * 100:.0f} %"


def _validate(
    route: RouteProfile,
    candidates: Sequence[CandidateStation],
    vehicle: EnergyVehicle,
    settings: PlannerSettings,
    starting_soc: float,
) -> None:
    def finite(*values: float) -> bool:
        return all(math.isfinite(v) for v in values)

    if not (finite(route.distance_km, route.duration_minutes) and route.distance_km >= 0):
        raise ValueError("route distance must be a finite number >= 0.")
    if route.duration_minutes < 0:
        raise ValueError("route duration must be >= 0.")
    if not (finite(starting_soc) and 0.0 <= starting_soc <= 1.0):
        raise ValueError("starting_soc must be in [0, 1].")
    s = settings
    if not finite(
        s.reserve_soc,
        s.charge_to_soc,
        s.stop_overhead_minutes,
        s.detour_road_factor,
        s.detour_speed_kmh,
        s.soc_grid_step,
    ):
        raise ValueError("planner settings must be finite numbers.")
    if not 0.0 <= s.reserve_soc < s.charge_to_soc <= max_charge_soc(vehicle) + _EPS:
        raise ValueError("need 0 <= reserve_soc < charge_to_soc <= the vehicle's max_charge_soc.")
    if not 0.0 < s.soc_grid_step <= 1.0:
        raise ValueError("soc_grid_step must be in (0, 1].")
    if s.stop_overhead_minutes < 0 or s.detour_road_factor < 0 or s.detour_speed_kmh <= 0:
        raise ValueError("overhead and road factor must be >= 0, detour speed > 0.")
    for c in candidates:
        if not (finite(c.power_kw) and c.power_kw > 0):
            raise ValueError(f"candidate {c.station_id} needs a known power > 0 (D12).")
        if not (
            finite(c.distance_along_route_km, c.distance_from_route_km)
            and c.distance_along_route_km >= 0
            and c.distance_from_route_km >= 0
        ):
            raise ValueError(f"candidate {c.station_id} has an invalid route position.")


__all__ = ["optimize"]
