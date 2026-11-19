"""Optimizer behaviour (phase4_plan.md §3 F3, §4.3): every PRD §9 optimizer row,
the infeasibility codes and limiting segments, the SoC grid, tie-breaks, input
validation, and optimality against an unpruned reference search."""

from __future__ import annotations

import math
import random
from functools import cache

import pytest

from app.modules.energy import EnergyVehicle, charging_minutes, soc_used
from app.modules.routing.optimizer import optimize
from app.modules.routing.plan_types import (
    CandidateStation,
    FeasiblePlan,
    InfeasiblePlan,
    PlannerSettings,
    RouteProfile,
)
from tests.routing.conftest import default_settings, station

KM = 0.20 / 71.0  # SoC per km for the bZ (phase4_plan.md §5)


def _feasible(result: object) -> FeasiblePlan:
    assert isinstance(result, FeasiblePlan), result
    return result


def _infeasible(result: object) -> InfeasiblePlan:
    assert isinstance(result, InfeasiblePlan), result
    return result


# --------------------------------------------------------------------------- PRD §9 rows


def test_start_at_100_percent_is_not_capped_by_charge_to(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    # 300 km uses 0.845: only a 100 % start (above charge-to 0.80) makes it.
    plan = _feasible(optimize(RouteProfile(300.0, 200.0), [], bz, settings, 1.00))
    assert plan.stops == ()
    assert plan.arrival_soc == pytest.approx(1.00 - 300 * KM)


def test_start_at_100_percent_with_a_stop(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    plan = _feasible(
        optimize(RouteProfile(400.0, 270.0), [station("M", 200, 0, 150)], bz, settings, 1.00)
    )
    (stop,) = plan.stops
    assert stop.arrival_soc == pytest.approx(1.00 - 200 * KM)  # 0.437, from the full start
    assert stop.departure_soc == 0.72  # ceil(0.15 + 200 km) on the grid


@pytest.mark.parametrize("starting_soc", [0.0, 0.10, 0.15])
def test_start_at_or_below_reserve_is_too_low(
    bz: EnergyVehicle, settings: PlannerSettings, starting_soc: float
) -> None:
    result = _infeasible(
        optimize(RouteProfile(10.0, 10.0), [station("M", 5, 0, 150)], bz, settings, starting_soc)
    )
    assert result.code == "STARTING_SOC_TOO_LOW"
    assert result.limiting_segment is None


def test_no_charging_needed_has_zero_stops_and_exact_arrival(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    # Candidates are ignored when the destination is in reach.
    plan = _feasible(
        optimize(RouteProfile(80.0, 60.0), [station("M", 40, 0, 150)], bz, settings, 0.72)
    )
    assert plan.stops == ()
    assert plan.arrival_soc == 0.72 - soc_used(bz, 80.0)
    assert plan.total_minutes == 60.0


def test_no_candidates_when_charging_is_needed(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    """Also the "chargers nearby but incompatible" row: F4 drops incompatible
    stations, so the optimizer sees none."""

    result = _infeasible(optimize(RouteProfile(400.0, 270.0), [], bz, settings, 0.80))
    assert result.code == "NO_COMPATIBLE_CHARGER_IN_RANGE"
    seg = result.limiting_segment
    assert seg is not None
    assert seg.from_km == 0.0
    assert seg.to_km == pytest.approx((0.80 - 0.15) / KM)  # 230.75 km: the reserve point
    assert seg.required_soc == pytest.approx(0.15 + 400 * KM)  # to the destination
    assert seg.available_soc == 0.80


def test_charger_beyond_reach(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    # The reserve point is 230.75 km (284 km with reserve 0); the charger is at 300.
    result = _infeasible(
        optimize(RouteProfile(500.0, 330.0), [station("F", 300, 0.5, 150)], bz, settings, 0.80)
    )
    assert result.code == "NO_COMPATIBLE_CHARGER_IN_RANGE"
    seg = result.limiting_segment
    assert seg is not None
    assert (seg.from_km, seg.to_km) == (0.0, pytest.approx(230.75, abs=0.01))
    assert seg.required_soc == pytest.approx(0.15 + (300 + 2 * 0.5 * 1.3) * KM)
    assert seg.required_soc > seg.available_soc == 0.80


def test_gap_to_destination_too_long(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    candidates = [station("A", 100, 0, 150), station("B", 200, 0, 150)]
    result = _infeasible(optimize(RouteProfile(700.0, 450.0), candidates, bz, settings, 0.80))
    assert result.code == "DESTINATION_UNREACHABLE"
    seg = result.limiting_segment
    assert seg is not None
    assert (seg.from_km, seg.to_km) == (200, 700.0)
    assert seg.required_soc == pytest.approx(0.15 + 500 * KM)
    assert seg.available_soc == 0.80


def test_gap_between_chargers_too_long(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    candidates = [
        station("A", 100, 0, 150),
        station("B", 200, 0, 150),
        station("C", 600, 0.4, 150),
    ]
    result = _infeasible(optimize(RouteProfile(700.0, 450.0), candidates, bz, settings, 0.80))
    assert result.code == "DESTINATION_UNREACHABLE"
    seg = result.limiting_segment
    assert seg is not None
    assert (seg.from_km, seg.to_km) == (200, 600)
    assert seg.required_soc == pytest.approx(0.15 + (400 + 2 * 0.4 * 1.3) * KM)
    assert seg.available_soc == 0.80


def test_reserve_unreachable(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    """Feasible only by arriving below the reserve: 200 → 450 km uses 0.704, so it
    needs 0.854 with the reserve but only 0.80 can be charged."""

    result = _infeasible(
        optimize(RouteProfile(450.0, 300.0), [station("M", 200, 0, 150)], bz, settings, 0.80)
    )
    assert result.code == "RESERVE_SOC_UNREACHABLE"
    seg = result.limiting_segment
    assert seg is not None
    assert (seg.from_km, seg.to_km) == (200, 450.0)
    assert seg.required_soc == pytest.approx(0.15 + 250 * KM)
    assert seg.available_soc == 0.80

    # With reserve 0 the same trip is a valid plan.
    relaxed = optimize(
        RouteProfile(450.0, 300.0),
        [station("M", 200, 0, 150)],
        bz,
        default_settings(reserve_soc=0.0),
        0.80,
    )
    assert isinstance(relaxed, FeasiblePlan)


def test_reserve_unreachable_on_the_first_leg(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    # 0.70 start: the charger at 200 km is reachable only below the reserve.
    result = _infeasible(
        optimize(RouteProfile(300.0, 200.0), [station("M", 200, 0, 150)], bz, settings, 0.70)
    )
    assert result.code == "RESERVE_SOC_UNREACHABLE"
    seg = result.limiting_segment
    assert seg is not None
    assert (seg.from_km, seg.to_km) == (0.0, 200)
    assert seg.available_soc == 0.70


# --------------------------------------------------------------------------- grid, ties


def test_departure_is_the_smallest_grid_value_that_works(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    candidates = [station("B", 220, 1.0, 250)]
    plan = _feasible(optimize(RouteProfile(400.0, 270.0), candidates, bz, settings, 0.80))
    (stop,) = plan.stops
    needed = 0.15 + 180 * KM  # 0.65704
    assert stop.departure_soc == math.ceil(needed * 100) / 100 == 0.66
    assert round(stop.departure_soc * 100, 9).is_integer()
    assert plan.arrival_soc >= 0.15
    # One grid step lower would miss the reserve.
    assert stop.departure_soc - 0.01 - 180 * KM < 0.15


def test_coarser_grid_rounds_departures_up(bz: EnergyVehicle) -> None:
    settings = default_settings(soc_grid_step=0.05)
    plan = _feasible(
        optimize(RouteProfile(400.0, 270.0), [station("B", 220, 1.0, 250)], bz, settings, 0.80)
    )
    assert plan.stops[0].departure_soc == 0.70


def test_identical_stations_tie_break_on_station_id(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    a, b = station("twin-a", 200, 0, 150), station("twin-b", 200, 0, 150)
    first = min(a, b, key=lambda s: s.station_id)
    for order in ([a, b], [b, a]):
        plan = _feasible(optimize(RouteProfile(400.0, 270.0), order, bz, settings, 0.80))
        assert [s.station for s in plan.stops] == [first]


def test_slow_station_avoided_when_a_fast_one_works(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    candidates = [station("slow", 200, 0, 50), station("fast", 200.5, 0, 150)]
    plan = _feasible(optimize(RouteProfile(400.0, 270.0), candidates, bz, settings, 0.80))
    assert [s.station.name for s in plan.stops] == ["fast"]


def test_detour_energy_is_charged_to_the_arriving_leg(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    plan = _feasible(
        optimize(RouteProfile(400.0, 270.0), [station("M", 200, 3.0, 150)], bz, settings, 0.80)
    )
    (stop,) = plan.stops
    assert stop.detour_km == pytest.approx(7.8)
    assert stop.arrival_soc == pytest.approx(0.80 - 207.8 * KM)
    assert plan.arrival_soc == pytest.approx(stop.departure_soc - 200 * KM)
    assert plan.energy_kwh == pytest.approx(407.8 * 0.20)
    assert plan.total_minutes == pytest.approx(270 + stop.charging_minutes + 7.8 / 50 * 60 + 5)


def test_unsorted_input_is_planned_in_route_order(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    candidates = [station("Q", 330, 0, 150), station("P", 220, 0, 150)]
    plan = _feasible(
        optimize(
            RouteProfile(500.0, 300.0), candidates, bz, default_settings(charge_to_soc=1.0), 0.8
        )
    )
    assert [s.station.name for s in plan.stops] == ["P", "Q"]


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "overrides",
    [
        {"reserve_soc": 0.80},  # reserve >= charge-to
        {"reserve_soc": -0.01},
        {"charge_to_soc": 1.01},  # above the curve's last point
        {"soc_grid_step": 0.0},
        {"detour_speed_kmh": 0.0},
        {"stop_overhead_minutes": -1.0},
        {"reserve_soc": math.nan},
    ],
)
def test_invalid_settings_raise(bz: EnergyVehicle, overrides: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        optimize(RouteProfile(100.0, 60.0), [], bz, default_settings(**overrides), 0.8)


@pytest.mark.parametrize("starting_soc", [-0.1, 1.1, math.nan])
def test_invalid_starting_soc_raises(
    bz: EnergyVehicle, settings: PlannerSettings, starting_soc: float
) -> None:
    with pytest.raises(ValueError):
        optimize(RouteProfile(100.0, 60.0), [], bz, settings, starting_soc)


@pytest.mark.parametrize(
    "candidate",
    [
        station("zero", 50, 0, 0.0),
        station("negative-along", -1, 0, 150),
        station("negative-off", 50, -1, 150),
    ],
)
def test_invalid_candidates_raise(
    bz: EnergyVehicle, settings: PlannerSettings, candidate: CandidateStation
) -> None:
    with pytest.raises(ValueError):
        optimize(RouteProfile(100.0, 60.0), [candidate], bz, settings, 0.8)


def test_invalid_route_raises(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    with pytest.raises(ValueError):
        optimize(RouteProfile(-1.0, 60.0), [], bz, settings, 0.8)
    with pytest.raises(ValueError):
        optimize(RouteProfile(10.0, -1.0), [], bz, settings, 0.8)


# --------------------------------------------------------------------------- optimality


def _reference_extra_minutes(
    route: RouteProfile,
    candidates: list[CandidateStation],
    vehicle: EnergyVehicle,
    settings: PlannerSettings,
    starting_soc: float,
) -> float | None:
    """Unpruned search: from every (station, grid departure) try every later
    station and every grid departure. Returns least charging + detour + overhead."""

    nodes = sorted(candidates, key=lambda c: (c.distance_along_route_km, c.station_id))
    step = settings.soc_grid_step
    grid = [
        round(k * step, 12) for k in range(math.floor(settings.charge_to_soc / step + 1e-9) + 1)
    ]
    reserve = settings.reserve_soc - 1e-9

    def go(position: float, soc: float, first: int) -> float | None:
        best: float | None = None
        if soc - soc_used(vehicle, route.distance_km - position) >= reserve:
            best = 0.0
        for j in range(first, len(nodes)):
            n = nodes[j]
            detour_km = 2 * n.distance_from_route_km * settings.detour_road_factor
            arrival = soc - soc_used(vehicle, n.distance_along_route_km - position + detour_km)
            if arrival < reserve:
                continue
            for k, departure in enumerate(grid):
                if departure <= arrival + 1e-12:
                    continue
                rest = after(j, k)
                if rest is None:
                    continue
                cost = (
                    charging_minutes(vehicle, max(arrival, 0.0), departure, n.power_kw)
                    + detour_km / settings.detour_speed_kmh * 60
                    + settings.stop_overhead_minutes
                    + rest
                )
                if best is None or cost < best:
                    best = cost
        return best

    @cache
    def after(j: int, k: int) -> float | None:
        return go(nodes[j].distance_along_route_km, grid[k], j + 1)

    return go(0.0, starting_soc, 0)


@pytest.mark.parametrize("seed", range(12))
def test_matches_unpruned_reference_search(bz: EnergyVehicle, seed: int) -> None:
    rng = random.Random(seed)
    settings = default_settings(
        soc_grid_step=0.02,
        reserve_soc=rng.choice([0.0, 0.10, 0.15]),
        charge_to_soc=rng.choice([0.80, 0.90, 1.00]),
    )
    length = rng.uniform(300, 650)
    candidates = [
        station(
            f"s{seed}-{n}",
            round(rng.uniform(0, length), 1),
            round(rng.uniform(0, 3), 2),
            rng.choice([50, 62.5, 120, 150, 250]),
        )
        for n in range(6)
    ]
    route = RouteProfile(length, length * 0.7)
    start = rng.choice([0.4, 0.6, 0.8, 1.0])

    expected = _reference_extra_minutes(route, candidates, bz, settings, start)
    result = optimize(route, candidates, bz, settings, start)
    if expected is None:
        assert isinstance(result, InfeasiblePlan)
        assert result.code != "STARTING_SOC_TOO_LOW"
    else:
        plan = _feasible(result)
        assert plan.total_minutes - route.duration_minutes == pytest.approx(expected, abs=1e-6)
        assert plan.arrival_soc >= settings.reserve_soc - 1e-9
        assert all(s.arrival_soc >= settings.reserve_soc - 1e-9 for s in plan.stops)
