"""The hand-checked trips of phase4_plan.md §5 and the "two short stops" case
(§3 F3), asserted exactly: chosen stops, SoCs to 3 decimals, minutes to 0.01."""

from __future__ import annotations

import pytest

from app.modules.energy import EnergyVehicle
from app.modules.routing.optimizer import optimize
from app.modules.routing.plan_types import FeasiblePlan, PlannerSettings, RouteProfile
from tests.routing.conftest import default_settings, frederick_toronto_route, station


def _feasible(result: object) -> FeasiblePlan:
    assert isinstance(result, FeasiblePlan), result
    return result


def test_trip_1_local_no_charge(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    plan = _feasible(optimize(RouteProfile(80.0, 60.0), [], bz, settings, 0.72))

    assert plan.stops == ()
    assert plan.arrival_soc == pytest.approx(0.72 - 16.0 / 71.0, abs=1e-9)  # 0.49465
    assert round(plan.arrival_soc, 3) == 0.495
    assert plan.total_minutes == pytest.approx(60.0)
    assert plan.charging_minutes == plan.detour_minutes == plan.overhead_minutes == 0
    assert plan.energy_kwh == pytest.approx(16.0)


def test_trip_2_one_stop_at_b(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    # X (CHAdeMO) is excluded by F4 and never reaches the optimizer.
    candidates = [
        station("L", 120, 0.2, 50),
        station("A", 150, 0.5, 150),
        station("B", 220, 1.0, 250, connector="NACS"),
    ]
    plan = _feasible(optimize(RouteProfile(400.0, 270.0), candidates, bz, settings, 0.80))

    assert [s.station.name for s in plan.stops] == ["B"]
    stop = plan.stops[0]
    assert stop.arrival_soc == pytest.approx(0.17296, abs=5e-6)
    assert stop.departure_soc == 0.66
    assert stop.charging_minutes == pytest.approx(16.648, abs=0.01)
    assert stop.detour_km == pytest.approx(2.6)
    assert stop.detour_minutes == pytest.approx(3.12)
    assert plan.overhead_minutes == 5
    assert plan.total_minutes == pytest.approx(294.77, abs=0.01)
    assert plan.arrival_soc == pytest.approx(0.15296, abs=5e-6)


def test_trip_3_frederick_toronto_three_stops(bz: EnergyVehicle, settings: PlannerSettings) -> None:
    route = frederick_toronto_route()
    assert route.distance_km == pytest.approx(748.1079)
    assert route.duration_minutes == pytest.approx(514.845)
    candidates = [
        station("H", 130, 0.6, 250, connector="NACS"),
        station("W", 260, 0.8, 150),
        station("E", 330, 0.4, 62.5),
        station("B", 420, 1.2, 250, connector="NACS"),
        station("Q", 560, 0.5, 150),
        station("N", 640, 0.7, 250, connector="NACS"),
    ]
    plan = _feasible(optimize(route, candidates, bz, settings, 0.90))

    assert [s.station.name for s in plan.stops] == ["W", "B", "Q"]
    expected = [  # arrival, departure, charging, detour minutes
        (0.16175, 0.61, 14.617, 2.496),
        (0.15051, 0.55, 12.443, 3.744),
        (0.15197, 0.68, 18.269, 1.560),
    ]
    for stop, (arrival, departure, charging, detour) in zip(plan.stops, expected, strict=True):
        assert round(stop.arrival_soc, 3) == round(arrival, 3)
        assert stop.departure_soc == departure
        assert stop.charging_minutes == pytest.approx(charging, abs=0.01)
        assert stop.detour_minutes == pytest.approx(detour, abs=0.01)
    assert plan.charging_minutes == pytest.approx(45.329, abs=0.01)
    assert plan.detour_minutes == pytest.approx(7.800, abs=0.01)
    assert plan.overhead_minutes == 15
    # §5 rounds the route to 748.1 km and 514.8 min; the fixture's exact values
    # give 582.97 (API 583) and arrival 0.15012 (API 0.150).
    assert plan.total_minutes - route.duration_minutes == pytest.approx(68.13, abs=0.01)
    assert plan.total_minutes == pytest.approx(582.97, abs=0.01)
    assert round(plan.arrival_soc, 3) == 0.150
    assert plan.energy_kwh == pytest.approx(150.92, abs=0.01)


def test_two_short_stops_beat_one_long_stop(bz: EnergyVehicle) -> None:
    """PRD §9: P then Q (0.18→0.46, 0.15→0.63), 34.2 extra minutes, beats P only
    (0.18→0.94, 45.2 extra minutes)."""

    settings = default_settings(charge_to_soc=1.00)
    route = RouteProfile(500.0, 300.0)
    p, q = station("P", 220, 0.0, 150), station("Q", 330, 0.0, 150)

    plan = _feasible(optimize(route, [p, q], bz, settings, 0.80))
    assert [s.station.name for s in plan.stops] == ["P", "Q"]
    assert [round(s.arrival_soc, 2) for s in plan.stops] == [0.18, 0.15]
    assert [s.departure_soc for s in plan.stops] == [0.46, 0.63]
    assert plan.total_minutes - route.duration_minutes == pytest.approx(34.2, abs=0.05)

    p_only = _feasible(optimize(route, [p], bz, settings, 0.80))
    assert [(round(s.arrival_soc, 2), s.departure_soc) for s in p_only.stops] == [(0.18, 0.94)]
    assert p_only.total_minutes - route.duration_minutes == pytest.approx(45.2, abs=0.05)
    assert plan.total_minutes < p_only.total_minutes
